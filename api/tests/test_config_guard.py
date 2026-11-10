"""生产环境配置守卫：不安全配置必须在启动期炸掉。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings

from core.config import Settings

_SAFE_PROD = {
    "app_env": "prod",
    "debug": False,
    "allow_anonymous": False,
    "jwt_secret": "9f" * 32,
    "cors_origins": ["https://studio.example.com"],
}


def _prod(**overrides: object) -> Settings:
    return Settings(**(_SAFE_PROD | overrides))  # type: ignore[arg-type]


def test_safe_prod_boots() -> None:
    settings = _prod()
    assert settings.app_env == "prod"
    assert settings.anonymous_allowed is False


def test_prod_rejects_anonymous() -> None:
    with pytest.raises(ValueError, match="ALLOW_ANONYMOUS"):
        _prod(allow_anonymous=True)


def test_prod_rejects_sample_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _prod(jwt_secret="change-me-in-production-please-use-openssl-rand-hex-32")


def test_prod_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="长度不足"):
        _prod(jwt_secret="short")


def test_prod_rejects_wildcard_cors() -> None:
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _prod(cors_origins=["*"])


def test_prod_rejects_debug() -> None:
    with pytest.raises(ValueError, match="DEBUG"):
        _prod(debug=True)


def test_dev_stays_lenient() -> None:
    """dev 不做生产校验，否则本地开发会被卡死。"""
    settings = Settings(app_env="dev", debug=True, allow_anonymous=True)
    assert settings.anonymous_allowed is True


def test_jwt_algorithm_whitelist_rejects_none() -> None:
    """`JWT_ALGORITHM=none` 等于关掉签名校验，必须在启动期炸掉。

    它失效时没有任何症状：登录、刷新、鉴权全部照常工作，只是令牌不再被校验——
    任何人都能用任意 `sub` 自签一张票。这类「配置一改就等于没有鉴权」的开关
    和 `TRUSTED_PROXIES`、`ALLOW_ANONYMOUS` 同一类，不能等到线上才发现。
    """
    assert Settings(jwt_algorithm=" HS256 ").jwt_algorithm == "HS256"  # 容忍空格
    with pytest.raises(ValueError, match="不签名"):
        Settings(jwt_algorithm="none")
    # 拼错的名字同样当场失败，而不是等第一次登录报 Algorithm not supported
    with pytest.raises(ValueError, match="不在允许的算法列表"):
        Settings(jwt_algorithm="HS25G")


def test_prod_guard_imports_active_driver_before_checking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守卫不能只查「已经 import 过」的驱动配置。

    登记表是按 import 触发的，而 `infra.storage.s3` 平时没人 import——它是
    `load_driver` 在第一次用到时才加载的。于是 `STORAGE_DRIVER=s3` 漏配密钥时：
    服务正常启动、健康检查 200、第一张素材上传才 502。这类「配置错在启动后才暴露」
    是最难排查的一种，必须在启动期炸掉。
    """
    from core.config import settings
    from infra import driver_config as dc

    # 模拟「进程刚起来、S3 驱动还没被 import 过」：登记表清空 + 模块踢出缓存。
    # 不动原列表对象，teardown 时 monkeypatch 会还原属性，其它测试看到的登记表不变。
    monkeypatch.setattr(dc, "_REGISTRY", [])
    monkeypatch.delitem(sys.modules, "infra.storage.s3", raising=False)
    for key in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, "storage_driver", "s3")
    monkeypatch.setattr(settings, "ai_providers", "")

    with pytest.raises(RuntimeError, match="S3Settings"):
        dc.ensure_prod_driver_settings("prod")

    assert "infra.storage.s3" in sys.modules, "守卫没有主动 import 启用的驱动"


def test_default_runtime_paths_do_not_depend_on_cwd() -> None:
    """默认 sqlite / 存储路径必须是绝对路径（锚定 api/）。

    相对路径 `./var/app.db` 会随 cwd 变化：在 api/ 下启动读到的是正式开发库，
    在 api/src 下启动则新建一个空库——登录 401、列表为空，但数据其实还在。
    """
    from pathlib import Path

    db_default = str(Settings.model_fields["db_url"].default)
    storage_default = str(Settings.model_fields["storage_local_root"].default)

    db_needle = db_default.replace("\\", "/")
    assert "var/app.db" in db_needle
    db_path = Path(db_default.split("///", 1)[1]) if "///" in db_default else Path(db_default)
    assert db_path.is_absolute(), f"db_url 默认值不是绝对路径: {db_default}"

    assert "var/storage" in storage_default.replace("\\", "/")
    assert Path(storage_default).is_absolute(), f"storage 默认值不是绝对路径: {storage_default}"


# --------------------------------------------------------------------------- #
# 配置卫生：三条机械守卫
#
# 这三条不是洁癖，各自对应一次真实故障或一次真实的「加了配置项但没人读」：
# - 字段没人读 → `GENERATION_TIMEOUT_SEC` 写进配置半年，其实零处引用，
#   真实超时是 max_polls×interval（300 秒），配置里写着 1800 秒；
# - 默认值指向盘符 → `Z:/yufan_ai_studio` 换台机器就报「归档盘不可用」；
# - `.env.example` 漂移 → 部署时不知道要配什么，配了拼错的键也不报错（extra="ignore"）。
# --------------------------------------------------------------------------- #
_SRC = Path(__file__).resolve().parents[1] / "src"
_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
_API_ROOT = Path(__file__).resolve().parents[1]

# 只认「仓库内的绝对路径」以外的机器路径：盘符、Linux 家目录、挂载点
_FOREIGN_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:home|mnt|Users|opt|srv|var|tmp)/)")


def _driver_settings() -> tuple[type[BaseSettings], ...]:
    """驱动私有配置类：自动发现，不再维护手写清单。

    以前这里是一份元组，新增驱动必须记得加进来——`ArchiveS3Settings` 就是这么
    漏了半年：`S3_ARCHIVE_*` 写进 `.env.example` 反而被判「无法识别的键」。
    现在靠 `DriverSettings.__init_subclass__` 自动登记，本函数只负责把模块
    import 进来（登记是 import 副作用）。

    `extra_modules` 是业务模块自带的驱动配置：`ImageAssetSettings` 在
    `modules/image/pipeline.py`，不在 `infra/` 扫描范围。
    """
    from infra.driver_config import import_all_driver_settings

    return import_all_driver_settings(extra_modules=("modules.image.pipeline",))


def _all_yaml_fields() -> list[tuple[str, str, object]]:
    """(环境变量键名, 字段名, 默认值) —— 全局与驱动私有配置合并。"""
    items: list[tuple[str, str, object]] = []
    for name, field in Settings.model_fields.items():
        items.append((name.upper(), name, field.default))
    for cls in _driver_settings():
        prefix = str(cls.model_config.get("env_prefix") or "")
        for name, field in cls.model_fields.items():
            items.append((f"{prefix}{name.upper()}", name, field.default))
    return items


def test_every_setting_is_referenced() -> None:
    """每个配置项都必须至少被读一次。

    「加了字段、写了注释、没人读」是最难发现的一类错：配置看上去生效了，
    实际是另一套逻辑在跑（`GENERATION_TIMEOUT_SEC` 就是这么躺了半年）。
    两类配置的读法不同，分开查：

    - 全局：`settings.x` / `get_settings().x`（属性内部还会用 `self.x`）；
    - 驱动私有：`get_xxx_settings().x` 或局部别名 `config.x`，都在驱动自己的文件里。
    """
    if not _SRC.is_dir():  # pragma: no cover - 只在非标准布局下跳过
        pytest.skip("src 目录不存在")

    source = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in _SRC.rglob("*.py")
    )
    unused = [
        name
        for key, name, _default in _all_yaml_fields()
        if key in {f.upper() for f in Settings.model_fields}  # 全局配置
        and not re.search(rf"(?:settings|get_settings\(\))\.{name}\b", source)
        and not re.search(rf"self\.{name}\b", source)
    ]

    # 驱动私有配置只在自己的驱动文件里读，逐个文件核对
    for cls in _driver_settings():
        module = sys.modules[cls.__module__]
        driver_source = Path(str(module.__file__)).read_text(encoding="utf-8")
        unused.extend(
            name
            for name in cls.model_fields
            if not re.search(rf"\.{name}\b", driver_source)
        )

    assert not unused, (
        f"这些配置项没有任何代码读取（写了等于没写）：{unused}。"
        "要么接上使用点，要么删掉字段"
    )


def test_env_example_lists_every_key() -> None:
    """.env.example 必须列全每个配置项（含驱动私有配置）。

    允许写成注释（`# DB_URL=...`），意思是「有默认值，通常不用配」。
    """
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [
        key
        for key, _name, _default in _all_yaml_fields()
        if not re.search(rf"^\s*#?\s*{re.escape(key)}=", text, flags=re.MULTILINE)
    ]
    assert not missing, f".env.example 缺少这些配置项：{missing}"


def test_env_example_has_no_unknown_key() -> None:
    """反向：示例里出现的（未注释的）键必须真能被某个 Settings 解析。

    `extra="ignore"` 让拼错的键静默失效 —— 部署时写了 `AGNES_KEY=xxx`
    不会报错，只会在第一次生图时提示「未配置 AGNES_API_KEY」。
    """
    known = {key for key, _name, _default in _all_yaml_fields()}
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    unknown = [
        match.group(1)
        for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.MULTILINE)
        if match.group(1) not in known
    ]
    assert not unknown, f".env.example 里有无法被任何 Settings 识别的键：{unknown}"


def test_defaults_are_not_machine_paths() -> None:
    """默认值只能锚定**仓库内**的路径，不能指向某台机器的盘符或家目录。

    归档根目录曾经默认 `Z:/yufan_ai_studio`、外部运行时目录曾默认
    `E:/yufan-ai`：在作者那台机器上一切正常，换一台就报「归档盘不可用」，
    而且设计上明确不回退本地保存 —— 于是整个归档功能不可用。
    绝对路径本身不是错（`api/var/...` 就该是绝对的，避免随 cwd 变化），
    错的是它指向了**仓库之外**。
    """
    bad: list[str] = []
    for key, _name, default in _all_yaml_fields():
        if not isinstance(default, str) or not default:
            continue
        # sqlite URL 里嵌了一条路径，去掉 scheme 再判
        value = re.sub(r"^[a-z+]+:///+", "", default)
        if not _FOREIGN_PATH.match(value):
            continue
        if not Path(value).is_absolute() or not Path(value).resolve().is_relative_to(
            _API_ROOT
        ):
            bad.append(f"{key}={default}")
    assert not bad, (
        f"这些默认值指向了仓库外的机器路径，换台机器就会失败：{bad}。"
        "留空并在使用处报错（带配置项名），或锚定到 api/var 下"
    )


# --------------------------------------------------------------------------- #
# D2：忽略规则不能吞掉源码
#
# 真实事故：新模块把运行时资产目录写进 `.gitignore` 的宽泛规则里（比如 `var/`、
# `assets/`），本地一切正常、CI 也绿，但那个目录在别人机器上不存在——直到线上报
# 「文件找不到」。这类规则看不见摸不着，只能靠机器比对。
# --------------------------------------------------------------------------- #
def test_gitignore_does_not_swallow_sources() -> None:
    from pathlib import PurePosixPath

    root = _API_ROOT.parent
    ignore_file = root / ".gitignore"
    if not ignore_file.exists():  # pragma: no cover - 非仓库环境
        pytest.skip("没有 .gitignore")

    patterns = [
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    def ignored(rel: str) -> str | None:
        for pattern in patterns:
            if pattern.startswith("!"):
                continue
            needle = pattern.rstrip("/")
            if "/" not in needle:
                # 不含斜杠的规则在 git 里匹配任意层级的同名文件/目录
                if any(PurePosixPath(part).match(needle) for part in rel.split("/")):
                    return pattern
                continue
            if PurePosixPath(rel).match(needle) or rel.startswith(needle + "/"):
                return pattern
        return None

    sources = [p for p in (root / "api" / "src").rglob("*.py") if "__pycache__" not in p.parts]
    sources += [
        p
        for p in (root / "studio" / "src").rglob("*")
        if p.is_file() and p.suffix in {".ts", ".tsx"}
    ]
    offenders = [
        f"{p.relative_to(root).as_posix()} ← {pattern}"
        for p in sources
        if (pattern := ignored(p.relative_to(root).as_posix()))
    ]
    assert not offenders, (
        "这些源码被 .gitignore 吃了（换台机器就不存在）：\n  " + "\n  ".join(offenders)
    )
