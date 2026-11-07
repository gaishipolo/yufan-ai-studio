"""归档存储的 S3 / MinIO 驱动。

**和 `infra/storage/s3.py` 的区别**：那个存的是"用户上传的素材"，URL 要直接给浏览器
看（公开读 + 预签名）；这里存的是"归档与运行快照"，只给本机进程读，从不对外发 URL。
默认 `S3_BUCKET_MODE=separate` **分桶**：归档桶不开放匿名读，否则私有原片会顺着
公开策略漏出去。设成 `shared` 则两条通路共用 `S3_BUCKET`，本驱动自动加 `archive/`
前缀隔离，并复用 `infra/blob.py` 缓存的同一个 boto3 client —— 只在桶策略已按前缀
管控时才允许。

**`create()` 是集群级 CAS 原语**：发布版号、GC 的选主租约都靠它，
`core/coordination.py` 的 `BackendLeaderElector` 直接用，不需要另引 Redis。

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
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.exceptions import Conflict, UpstreamError
from core.logging import get_logger
from infra.archive.base import Location, checksum, read_json, write_json
from infra.archive.runs import runs_root
from infra.blob import s3_client
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
    # `archive_bucket` 不列进生产必填：shared 模式下它本来就该留空、改用素材桶。
    # 是否必填由 `__init__` 按 bucket_mode 判断，静态清单管不了这种条件依赖。
    prod_required_fields: ClassVar[tuple[str, ...]] = (
        "endpoint",
        "access_key",
        "secret_key",
    )

    endpoint: str = ""
    archive_bucket: str = ""
    archive_prefix: str = ""
    # 字段名必须带 archive_ 前缀：env 前缀是 `S3_`，写成 `cache_dir` 会变成
    # `S3_CACHE_DIR`，与 .env.example 里的 `S3_ARCHIVE_CACHE_DIR` 对不上（配置守卫抓到过）。
    archive_cache_dir: str = ""
    # separate（默认）= 独立私有桶；shared = 与素材桶共用一个桶，靠 `archive/` 前缀隔离。
    # shared 只适合「桶策略已按前缀管控」的部署，别在公开读桶上存私有原片。
    bucket_mode: str = "separate"
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
        mode = self.settings.bucket_mode.strip().lower()
        self._prefix = self.settings.archive_prefix.strip().strip("/")
        self._bucket = self.settings.archive_bucket.strip()
        if mode == "shared":
            # 单桶部署：与素材通路共用 S3_BUCKET，用命名空间前缀隔开两套 key。
            from infra.storage.s3 import get_s3_settings

            self._bucket = get_s3_settings().bucket.strip()
            namespace = "archive"
            self._prefix = f"{namespace}/{self._prefix}" if self._prefix else namespace
        elif mode != "separate":
            raise UpstreamError(
                f"S3_BUCKET_MODE 只能是 separate 或 shared，收到 {mode!r}"
            )
        if not self._bucket:
            raise UpstreamError(
                "归档对象存储未配置：separate 模式请设置 S3_ARCHIVE_BUCKET，"
                "shared 模式请设置 S3_BUCKET；不会回退到本地目录"
            )
        # 与素材通路共用同一个 client：连接参数相同就命中 `infra.blob` 的缓存。
        self._client = s3_client(
            endpoint=self.settings.endpoint,
            access_key=self.settings.access_key,
            secret_key=self.settings.secret_key,
            region=self.settings.region,
            use_ssl=self.settings.use_ssl,
            verify=self.settings.verify,
            factory=boto3.client,
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

    def create(self, location: Location, value: object) -> bool:
        """条件写（`If-None-Match: *`）：对象已存在时返回 `False`，不覆盖。

        这是远端驱动下唯一可靠的版号 CAS。`catalog_lease` 在远端退化为本机锁，
        多实例并发发布只能靠这个原子点收敛（T20）。MinIO 与 S3（2024 起）
        都支持 PutObject 的条件写；不支持的服务端会忽略这个头——
        那属于部署缺陷，由 `app doctor` 一类的自检暴露，不在这里猜。
        """
        workspace = self._workspace(location)
        if workspace is not None:
            if workspace.exists():
                return False
            write_json(workspace, value)
            return True
        key = self._key(location)
        body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _precondition_failed(exc):
                return False
            raise UpstreamError(f"归档写入失败（{self._display(key)}）：{exc}") from exc
        except BotoCoreError as exc:
            raise UpstreamError(f"归档连接失败：{exc}") from exc
        return True

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
            # 临时文件名必须**每次唯一**：取件走 `asyncio.to_thread(_materialized)`，
            # 同一进程的两个并发请求是两个线程在跑 `materialize`。共用
            # `{name}.{pid}.part` 时它们会写同一个路径——Windows 直接
            # PermissionError，POSIX 上则是先完成的那个 rename 把文件搬走，
            # 后一个 rename 找不到自己的临时文件（FileNotFoundError）。
            # 两种都是「并发取同一份字节时偶发 500」，且只在并发下出现。
            temporary = target.with_name(f"{name}.{uuid.uuid4().hex}.part")
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
            try:
                temporary.replace(target)
            except OSError:
                # 别人刚放好了同一份字节（key 是内容寻址的，内容必然相同）。
                # 目标已存在就算成功；只有目标也不在时才是真失败。
                temporary.unlink(missing_ok=True)
                if not target.is_file():
                    raise
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
            f".{uuid.uuid4().hex}.tmp"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self._bucket, key, str(target))
            return checksum(target)
        finally:
            target.unlink(missing_ok=True)

    def _cache_root(self) -> Path:
        raw = self.settings.archive_cache_dir.strip()
        if raw:
            return Path(raw)
        # api/src/infra/archive/s3.py → api/var/archive-cache
        return Path(__file__).parents[3] / "var" / "archive-cache"

    def _evict(self) -> None:
        """按 mtime 淘汰，直到总量回到上限以内。

        **删不掉就跳过**：Windows 上正在被 `FileResponse` / ffmpeg 读的文件
        不允许删除（PermissionError）。淘汰是尽力而为的运维动作，不能因为它
        失败就让这次取件跟着 500——缓存的意义是省一次下载，不是成为新的故障点。
        总额仍然照减：这份字节确实不在缓存里了（只是暂时删不掉），下一轮还会
        再试一次；把它一直算在总额里只会让每轮都重复淘汰同一批。
        """
        root = self._cache_root()
        if not root.is_dir():
            return
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for path in root.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        if total <= CACHE_LIMIT_BYTES:
            return
        entries.sort()
        for _, size, path in entries:
            if total <= CACHE_LIMIT_BYTES:
                break
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.debug("缓存文件暂时删不掉，跳过：%s（%s）", path, exc)
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


def _precondition_failed(exc: ClientError) -> bool:
    """条件写没抢到：对象已经存在。

    不同实现给的状态码不一致（S3 用 `PreconditionFailed`，部分网关回 412），
    两种都认，别让「版号被别人抢了」冒泡成 502。
    """
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return code in {"PreconditionFailed", "412"} or status == "412"


Driver = S3ArchiveStorage
