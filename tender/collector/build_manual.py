#!/usr/bin/env python3
"""从 .md 重生成 manual.html（招投标信息维护手册）

用法：
  python3 build_manual.py            # 用今天日期
  python3 build_manual.py --date 2026-08-08
"""
import sys, os, argparse, datetime
import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.join(HERE, "..", "projects", "cockpit", "site", "招投标信息维护手册.md")
HTML_PATH = os.path.join(HERE, "..", "projects", "cockpit", "site", "manual.html")

HTML_SHELL_HEAD = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>招投标信息 · 维护手册</title>
<style>
:root {
  --bg: #f5f7fa; --card: #fff; --text: #2c3e50; --muted: #7f8c9b;
  --primary: #1f4e79; --accent: #e67e22; --border: #e3e8ef; --code-bg: #f0f4f8;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  line-height: 1.7; font-size: 15px;
}
.container { max-width: 960px; margin: 0 auto; padding: 24px 20px 80px; }
header.doc-head {
  background: linear-gradient(135deg, #1f4e79 0%, #2c6aa8 100%);
  color: #fff; padding: 28px 24px; border-radius: 12px; margin-bottom: 24px;
}
header.doc-head h1 { margin: 0 0 6px; font-size: 24px; }
header.doc-head p { margin: 0; opacity: .85; font-size: 13px; }
article.markdown {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 24px 28px;
}
article.markdown h1 { font-size: 22px; border-bottom: 2px solid var(--primary); padding-bottom: 8px; margin-top: 0; }
article.markdown h2 {
  font-size: 18px; margin-top: 36px; padding: 8px 12px;
  background: #eef4fb; border-left: 4px solid var(--primary); border-radius: 4px;
}
article.markdown h3 { font-size: 16px; margin-top: 24px; color: var(--primary); }
article.markdown table {
  width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px;
}
article.markdown th { background: #eef4fb; text-align: left; }
article.markdown th, article.markdown td {
  border: 1px solid var(--border); padding: 7px 10px; vertical-align: top;
}
article.markdown code {
  background: var(--code-bg); padding: 2px 5px; border-radius: 4px;
  font-family: "SF Mono", Consolas, monospace; font-size: 13px;
}
article.markdown pre {
  background: #1e2a38; color: #e8eef5; padding: 14px 16px; border-radius: 8px;
  overflow-x: auto; font-size: 13px; line-height: 1.5;
}
article.markdown pre code { background: none; padding: 0; color: inherit; }
article.markdown blockquote {
  margin: 12px 0; padding: 8px 16px; border-left: 4px solid var(--accent);
  background: #fdf6ec; border-radius: 0 6px 6px 0; color: #6b5b3e;
}
article.markdown a { color: #2c6aa8; }
article.markdown ul, article.markdown ol { padding-left: 24px; }
article.markdown hr { border: 0; border-top: 1px solid var(--border); margin: 28px 0; }
article.markdown .toc { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; }
article.markdown .toc ul { list-style: none; padding-left: 16px; }
footer.doc-foot { text-align: center; color: var(--muted); font-size: 12px; margin-top: 20px; }
@media (max-width: 768px) {
  .container { padding: 12px 10px 60px; }
  article.markdown { padding: 14px 16px; }
  article.markdown table { font-size: 13px; }
}
</style>
</head>
<body>
<div class="container">
<header class="doc-head"><h1>📖 招投标信息 · 维护手册</h1>
<p>维护：yankai agent ｜ 更新：{UPDATE_DATE} ｜ 数据源：tender_monitor/README.md</p></header>
<article class="markdown">
'''

HTML_SHELL_TAIL = '''
</article>
</div>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="manual.html 头部的更新日期 (YYYY-MM-DD)")
    args = ap.parse_args()

    with open(MD_PATH, encoding="utf-8") as f:
        md_text = f.read()

    # 用 python-markdown 渲染（tables, fenced_code, toc）
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )
    body = md.convert(md_text)

    head = HTML_SHELL_HEAD.replace("{UPDATE_DATE}", args.date)
    html = head + body + HTML_SHELL_TAIL

    # 备份旧版
    if os.path.exists(HTML_PATH):
        bak = HTML_PATH + f".bak_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"
        import shutil
        shutil.copy2(HTML_PATH, bak)
        print(f"  backup → {bak}")

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    sz = os.path.getsize(HTML_PATH)
    print(f"✅ manual.html 已重生成 ({sz} bytes, date={args.date})")


if __name__ == "__main__":
    sys.exit(main() or 0)