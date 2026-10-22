# -*- coding: utf-8 -*-
"""2026-10-01 经验样本库应用工具。

阶段：L1 手动触发（老板报告 → 我分析 → 入库 → 跑回灌）。

子命令：
  --list                                 列出所有经验
  --find SITE TYPE URL                   查匹配（site/notice_type/url 三段）
  --run EXP_ID URL                       跑某条经验的 extractor（fetch + 抽字段）

L2/L3 升级后此脚本可被 deploy.sh / backfill 调用。当前 L1 阶段是手动工具。
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SAMPLES_FILE = HERE / "experience_samples.json"


def load_samples():
    """加载所有经验样本。"""
    if not SAMPLES_FILE.exists():
        return []
    with open(SAMPLES_FILE, encoding="utf-8") as f:
        return json.load(f)


def find_matching(site, notice_type, url):
    """按 site/notice_type/url 三段过滤；只返回 status='verified' 的样本。"""
    samples = load_samples()
    matches = []
    for s in samples:
        if s.get("status") != "verified":
            continue
        if "site" in s and site != s["site"] and site not in s.get("sites", []):
            continue
        if "notice_types" in s and notice_type not in s["notice_types"]:
            continue
        url_pat = s.get("url_pattern", "")
        if url_pat and not re.search(url_pat, url):
            continue
        matches.append(s)
    return matches


def get(exp_id):
    """按 id 取单条经验。"""
    return next((s for s in load_samples() if s["id"] == exp_id), None)


def run_extractor(exp_id, url, timeout=15):
    """跑经验里 extractor_function 指定的 Python 函数对 URL 抓取 + 抽字段。

    extractor_function 格式：'a.b.c.func_name'（从 tender 包开始可 import）
    """
    exp = get(exp_id)
    if not exp:
        raise ValueError(f"未找到经验 {exp_id}")
    func_ref = exp.get("extractor_function", "")
    if not func_ref:
        raise ValueError(f"经验 {exp_id} 无 extractor_function 字段")

    # 路径示例：'tender.collector.scrapers.LegacyPublicResourceScraper._parse_award_page'
    # 支持两种形式：
    #   A. 模块函数：'pkg.mod.func_name'
    #   B. 类方法：'pkg.mod.ClassName.method_name'（ClassName 首字母大写）
    parts = func_ref.split(".")
    if len(parts) < 2:
        raise ValueError(f"extractor_function 格式错误：{func_ref}")
    func_name = parts[-1]
    second_last = parts[-2]

    # importlib.import_module 能正确处理点号路径
    # 路径解析：tender.collector.scrapers → 在 sys.path 里要能找到 `tender` 包
    # HERE = projects/cockpit/tender/collector/，所以 HERE.parent.parent = projects/cockpit/
    project_root = HERE.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    import importlib

    # 检测是否是类方法（second_last 首字母大写 + 无下划线）
    is_class_method = second_last[:1].isupper() and "_" not in second_last

    if is_class_method and len(parts) >= 3:
        module_path = ".".join(parts[:-2])
        class_name = second_last
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        # 静态方法可直接 cls.method，否则需实例化
        extractor = getattr(cls, func_name)
    else:
        module_path = ".".join(parts[:-1])
        mod = importlib.import_module(module_path)
        extractor = getattr(mod, func_name)

    import inspect
    req = urllib.request.Request(url, headers={"User-Agent": "yktender-experience-apply/1.0"})
    html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", errors="ignore")

    sig = inspect.signature(extractor)
    arity = len(sig.parameters)
    if arity == 1:
        return extractor(html)
    # 多参 extractor（如 _extract_bid_section_id(data, notice_type)、_infer_date(day_int)）
    # 不是「fetcher」，是「详情 API 抽字段」或「日期工具」，不能裸用 URL 跑
    return {
        "verifiable_via_url": False,
        "reason": f"extractor_function {func_ref} 需要 {arity} 个参数，不是单参 HTML fetcher。需走原代码路径验证。",
        "signature": str(sig),
        "verify_hint": f"查 {module_path}.{class_name if is_class_method else ''} 或读 fix_files 看完整流程".strip("."),
    }


def main():
    p = argparse.ArgumentParser(description="经验样本库应用工具（L1 手动）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列出所有经验（表格）")
    g.add_argument("--find", nargs=3, metavar=("SITE", "TYPE", "URL"), help="查匹配")
    g.add_argument("--run", nargs=2, metavar=("EXP_ID", "URL"), help="跑某条经验")
    g.add_argument("--show", metavar="EXP_ID", help="显示某条经验详情")
    args = p.parse_args()

    if args.list:
        print(f"=== 经验样本库（L1：{len(load_samples())} 条） ===")
        print(f"  文件：{SAMPLES_FILE}")
        print()
        print(f"  {'ID':<24} {'站':<32} {'状态':<10} {'验证时间':<12} {'标题'}")
        print(f"  {'-'*24} {'-'*32} {'-'*10} {'-'*12} {'-'*40}")
        for s in load_samples():
            print(f"  {s['id']:<24} {s.get('site','')[:30]:<32} {s.get('status','?'):<10} "
                  f"{s.get('verified_at','')[:10]:<12} {s.get('title','')[:60]}")

    elif args.find:
        site, ntype, url = args.find
        matches = find_matching(site, ntype, url)
        if not matches:
            print(f"  无匹配（site={site}, notice_type={ntype}）")
            return
        print(f"  匹配 {len(matches)} 条：")
        for s in matches:
            print(f"  ✓ {s['id']}: {s['title'][:60]}")

    elif args.show:
        s = get(args.show)
        if not s:
            print(f"  未找到 {args.show}")
            return
        print(json.dumps(s, ensure_ascii=False, indent=2))

    elif args.run:
        result = run_extractor(*args.run)
        if isinstance(result, dict):
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print(result)


if __name__ == "__main__":
    main()
