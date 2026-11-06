"""归档存储的 S3 / MinIO 驱动。

**和 `infra/storage/s3.py` 的区别**：那个存的是"用户上传的素材"，URL 要直接给浏览器
看（公开读 + 预签名）；这里存的是"归档与运行快照"，只给本机进程读，从不对外发 URL。
所以两者**分桶**：归档桶不开放匿名读，否则私有原片会顺着公开策略漏出去。

**为什么要 `materialize` 而不是直接给 URL**：桥接层（`libtv_runtime/bridge.mjs`）
要的是 `run/inputs/` 下的真实文件，还要重算 sha256；ffmpeg 派生预览也要真实路径。
远端存储没法交路径，只能先下到本地缓存。缓存按 key 命名——blob key 里就是 sha256，
天然不会读到旧版本字节。

已知边界：
- 缓存目录是**单机**的。多实例部署时每个实例各下一份，属可接受浪费；
  真正的共享由桶承担（不再有"共享盘单点"）。
- `root()` / `resolve()` 在远端驱动下没有意义，显式抛错而不是返回假路径：
  返回一个"看起来能用"的目录会让上游悄悄退化成单机行为。
- **枚举走 `list_keys`**：封装草稿/发布版本/运行索引都用它，不再 `root()` 上 glob。
- **`Path` 参数 = 运行工作区文件**（`run.json`、`inputs/*`、`records/*`），
  由本驱动的 `_workspace()` 就地读写，不进桶：桥接层与 ffmpeg 要真实文件。
- **取件走 `download_url`（预签名 307）**：归档桶是私有的，签名交给对象存储验；
  视频的 `Range` 也由它处理，首帧不用等整个文件下完（T19）。`materialize`
  只剩"执行器需要真实路径"这一种用途（桥接层、ffmpeg、派生预览）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from core.exceptions import Conflict, UpstreamError
from core.logging import get_logger
from infra.archive.base import Location, checksum, read_json, write_json
from infra.archive.runs import runs_root
from infra.driver_config import DriverSettings, driver_config

logger = get_logger(__name__)

# 物化缓存上限。超过就按 mtime 淘汰最旧的：缓存只是执行期的中转，
# 清掉只会让下一次多下载一次，不影响正确性。
CACHE_LIMIT_BYTES = 8 * 1024 * 1024 * 1024


class ArchiveS3Settings(DriverSettings):
    """端点与凭据复用 `S3_*`（同一套 MinIO 部署），桶单独指定。

    `S3_ARCHIVE_BUCKET` 是**必填**而不是默认等于 `S3_BUCKET`：归档桶必须与
    对外可读的素材桶分开，默认共用一个桶等于把私有原片放进公开读范围。
    """

    model_config = driver_config("S3")

    driver_kind: ClassVar[str] = "archive"
    driver_name: ClassVar[str] = "s3"
    prod_required_fields: ClassVar[tuple[str, ...]] = (
        "endpoint",
        "archive_bucket",
        "access_key",
        "secret_key",
    )

    endpoint: str = ""
    archive_bucket: str = ""
    archive_prefix: str = ""
    cache_dir: str = ""
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"
    use_ssl: bool = True
    verify: bool = True


@lru_cache
def get_archive_s3_settings() -> ArchiveS3Settings:
    return ArchiveS3Settings()


class S3ArchiveStorage:
    def __init__(self, settings: ArchiveS3Settings | None = None) -> None:
        self.settings = settings or get_archive_s3_settings()
        self._bucket = self.settings.archive_bucket.strip()
        if not self._bucket:
            raise UpstreamError(
                "归档对象存储未配置：请设置 S3_ARCHIVE_BUCKET（与素材桶分开），不会回退到本地目录"
            )
        self._prefix = self.settings.archive_prefix.strip().strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=self.settings.endpoint or None,
            aws_access_key_id=self.settings.access_key or None,
            aws_secret_access_key=self.settings.secret_key or None,
            region_name=self.settings.region,
            use_ssl=self.settings.use_ssl,
            verify=self.settings.verify,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    # ---------------------------------------------------------------- 位置
    def root(self) -> Path:
        raise UpstreamError("归档已切到对象存储：没有本地根目录，请改用 materialize()")

    def resolve(self, key: str) -> Path:
        raise UpstreamError("归档已切到对象存储：没有本地路径，请改用 materialize()")

    def _key(self, location: Location) -> str:
        if isinstance(location, Path):
            raise UpstreamError("对象存储驱动只接受 key，不接受本地路径")
        raw = location.strip().lstrip("/")
        if not raw or any(part in (".", "..") for part in raw.split("/")):
            raise UpstreamError(f"非法的归档 key：{location}")
        return f"{self._prefix}/{raw}" if self._prefix else raw

    def _display(self, key: str) -> str:
        """日志里不要每次都带上前缀。"""
        return key[len(self._prefix) + 1 :] if self._prefix else key

    def _workspace(self, location: object) -> Path | None:
        """`Path` 参数只可能是运行工作区的文件：那些必须留在本地，不进桶。

        协议里 key 是存储无关的，`Path` 保留给「真实本地文件」这一类调用
        （`run.json`、`inputs/*`、`records/*`、`outputs/*`）。远端驱动不猜路径：
        不在工作区内的绝对路径一律拒绝——把任意本地路径当"直接用这个文件"的许可，
        等于绕开了本地驱动里 `within()` 的全部防护。
        """
        if not isinstance(location, Path):
            return None
        root = runs_root().resolve()
        target = (location if location.is_absolute() else root / location).resolve()
        if not target.is_relative_to(root):
            raise UpstreamError(f"远端驱动只接受归档 key；本地路径必须位于运行工作区：{location}")
        return target

    # ---------------------------------------------------------------- 元数据
    def exists(self, location: Location) -> bool:
        workspace = self._workspace(location)
        if workspace is not None:
            return workspace.is_file()
        key = self._key(location)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return False
            raise UpstreamError(f"归档读取失败（{self._display(key)}）：{exc}") from exc
        except BotoCoreError as exc:
            raise UpstreamError(f"归档连接失败：{exc}") from exc
        return True

    def read(self, location: Location) -> object:
        workspace = self._workspace(location)
        if workspace is not None:
            return read_json(workspace)
        key = self._key(location)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            payload = response["Body"].read()
        except ClientError as exc:
            if _is_missing(exc):
                raise FileNotFoundError(self._display(key)) from exc
            raise UpstreamError(f"归档读取失败（{self._display(key)}）：{exc}") from exc
        except BotoCoreError as exc:
            raise UpstreamError(f"归档连接失败：{exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"归档对象不是有效 JSON：{self._display(key)}") from exc

    def write(self, location: Location, value: object) -> None:
        workspace = self._workspace(location)
        if workspace is not None:
            write_json(workspace, value)
            return
        key = self._key(location)
        body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
            )
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档写入失败（{self._display(key)}）：{exc}") from exc

    def list_keys(self, prefix: str, *, suffix: str = "") -> list[str]:
        """`ListObjectsV2` 分页枚举；前缀不存在返回空表（与本地驱动同语义）。"""
        base = self._key(prefix.strip("/") + "/")
        keys: list[str] = []
        try:
            for page in self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=base
            ):
                for item in page.get("Contents", []):
                    display = self._display(str(item["Key"]))
                    if display.endswith(suffix):
                        keys.append(display)
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档枚举失败（{self._display(base)}）：{exc}") from exc
        return sorted(keys)

    # ---------------------------------------------------------------- 字节
    def copy(self, source: Path, destination: Location, digest: str | None = None) -> str:
        workspace = self._workspace(destination)
        if workspace is not None:
            return _copy_file(source, workspace, digest or checksum(source))
        key = self._key(destination)
        expected = digest or checksum(source)
        if self.exists(key):
            # 内容寻址下重名即同内容；仍比对一次，防止有人手工往桶里塞了同名异物。
            if self._remote_digest(key) != expected:
                raise UpstreamError(f"归档对象与本次写入不一致：{self._display(key)}")
            return expected
        try:
            # 摘要写进对象元数据：同名对象已存在时比一次摘要不用重新下载
            self._client.upload_file(
                str(source), self._bucket, key, ExtraArgs={"Metadata": {"sha256": expected}}
            )
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档上传失败（{self._display(key)}）：{exc}") from exc
        return expected

    def link(self, source: Path, destination: Location, digest: str | None = None) -> str:
        """对象存储没有硬链接：归档目标是分片上传；工作区目标是本地硬链接。"""
        workspace = self._workspace(destination)
        if workspace is None:
            return self.copy(source, destination, digest)
        expected = digest or checksum(source)
        if workspace.exists():
            if checksum(workspace) != expected:
                raise Conflict("已有归档文件与本次输入不一致，禁止覆盖")
            return expected
        workspace.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 物化缓存与工作区通常同盘：一个 run 里同一份原片出现两次时，
            # 硬链接让它只占一份字节（执行器会给每个文件各算一次 sha256）。
            os.link(source, workspace)
            return expected
        except OSError:
            return _copy_file(source, workspace, expected)

    @contextmanager
    def materialize(self, location: Location) -> Iterator[Path]:
        """下到本地缓存再交出路径；缓存命中直接复用。

        缓存名用 key 的 sha1（key 本身可能很长且含分隔符），并保留原始扩展名——
        ffmpeg 与 `Image.open` 都靠扩展名判断容器格式，剥掉后缀会误判成 raw。
        """
        workspace = self._workspace(location)
        if workspace is not None:
            yield workspace
            return
        key = self._key(location)
        suffix = Path(self._display(key)).suffix
        name = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()
        target = self._cache_root() / f"{name}{suffix}"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{name}.{os.getpid()}.part")
            try:
                self._client.download_file(self._bucket, key, str(temporary))
            except ClientError as exc:
                temporary.unlink(missing_ok=True)
                if _is_missing(exc):
                    raise FileNotFoundError(self._display(key)) from exc
                raise UpstreamError(f"归档下载失败（{self._display(key)}）：{exc}") from exc
            except BotoCoreError as exc:
                temporary.unlink(missing_ok=True)
                raise UpstreamError(f"归档连接失败：{exc}") from exc
            temporary.replace(target)
            self._evict()
        try:
            yield target
        finally:
            # 有意不删：一个 run 会多次读同一张参考图（校验收据、编译提示词、派生预览），
            # 每次都重下等于把网络当磁盘用。淘汰交给 `_evict`。
            pass

    def download_url(self, location: Location, *, expires_in: int = 900) -> str | None:
        """预签名 GET：归档桶是私有的，不能像素材桶那样直接 `url_for`。

        运行工作区文件返回 `None`（它们压根不在桶里），由调用方直出本地文件。
        """
        workspace = self._workspace(location)
        if workspace is not None:
            return None
        key = self._key(location)
        try:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=expires_in,
                )
            )
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档签名失败（{self._display(key)}）：{exc}") from exc

    def modified_at(self, location: Location) -> datetime | None:
        workspace = self._workspace(location)
        if workspace is not None:
            if not workspace.is_file():
                return None
            return datetime.fromtimestamp(workspace.stat().st_mtime, UTC)
        key = self._key(location)
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise UpstreamError(f"归档读取失败（{self._display(key)}）：{exc}") from exc
        except BotoCoreError as exc:
            raise UpstreamError(f"归档连接失败：{exc}") from exc
        modified: datetime | None = head.get("LastModified")
        return modified

    def remove(self, location: Location) -> None:
        workspace = self._workspace(location)
        if workspace is not None:
            workspace.unlink(missing_ok=True)
            return
        key = self._key(location)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档删除失败（{self._display(key)}）：{exc}") from exc

    # ---------------------------------------------------------------- 内部
    def _remote_digest(self, key: str) -> str:
        """桶里对象的 sha256：优先读自定义元数据，缺失时下载重算。

        只在"同名对象已存在"这一条少见路径上调用，不必为它设计增量校验。
        """
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError(f"归档读取失败（{self._display(key)}）：{exc}") from exc
        declared = (head.get("Metadata") or {}).get("sha256", "")
        if declared:
            return str(declared)
        target = self._cache_root() / (
            f"verify-{hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self._bucket, key, str(target))
            return checksum(target)
        finally:
            target.unlink(missing_ok=True)

    def _cache_root(self) -> Path:
        raw = self.settings.cache_dir.strip()
        if raw:
            return Path(raw)
        # api/src/infra/archive/s3.py → api/var/archive-cache
        return Path(__file__).parents[3] / "var" / "archive-cache"

    def _evict(self) -> None:
        root = self._cache_root()
        if not root.is_dir():
            return
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for path in root.iterdir():
            if not path.is_file():
                continue
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        if total <= CACHE_LIMIT_BYTES:
            return
        entries.sort()
        for _, size, path in entries:
            if total <= CACHE_LIMIT_BYTES:
                break
            path.unlink(missing_ok=True)
            total -= size


def _copy_file(source: Path, target: Path, expected: str) -> str:
    """写工作区文件：先写临时文件再替换，避免执行器读到半份输入。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    if checksum(temporary) != expected:
        temporary.unlink(missing_ok=True)
        raise UpstreamError("工作区复制校验失败，请检查磁盘")
    temporary.replace(target)
    return expected


def _is_missing(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


Driver = S3ArchiveStorage
