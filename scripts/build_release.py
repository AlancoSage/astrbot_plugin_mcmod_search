"""构建可安装的版本 ZIP；不包含缓存、运行数据或旧版本安装包。"""

import ast
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "main.py", "metadata.yaml", "requirements.txt", "README.md",
)


def build_release():
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8-sig")
    match = re.search(r"(?m)^version:\s*(v\d+\.\d+\.\d+)\s*$", metadata)
    if not match:
        raise ValueError("metadata.yaml 中缺少有效版本号")
    version = match.group(1)
    source = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    registrations = [
        decorator for node in tree.body if isinstance(node, ast.ClassDef)
        for decorator in node.decorator_list if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name) and decorator.func.id == "register"
    ]
    if len(registrations) != 1 or ast.literal_eval(registrations[0].args[3]) != version:
        raise ValueError("main.py 与 metadata.yaml 的版本号不一致")
    for name in FILES:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(name)
    destination = ROOT / f"astrbot_plugin_mcmod_search-{version}.zip"
    if destination.exists():
        raise FileExistsError(f"版本安装包已存在，请更新版本号：{destination}")
    with ZipFile(destination, "x", ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("安装包完整性检查失败")
        for name in FILES:
            if archive.read(name) != (ROOT / name).read_bytes():
                raise ValueError(f"安装包内容校验失败：{name}")
    print(destination)
    return destination


if __name__ == "__main__":
    build_release()
