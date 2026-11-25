"""Regenerate docs/METHODOLOGY.md from backend/methodology.py:  python tools/make_docs.py"""
import os, sys
root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(root, "backend"))
import methodology as m  # noqa: E402

out = ["# AgroWatch Intelligence: methodology and data quality", "",
       "Generated from `backend/methodology.py`, the same register the dashboard serves at `/api/methodology`.", "",
       "## Data types", ""]
out += [f"- **{k}:** {v}" for k, v in m.CATEGORIES.items()]
out += ["", "## Principles", ""] + [f"{i}. {p}" for i, p in enumerate(m.PRINCIPLES, 1)]
out += ["", "## What is implemented where", ""] + [f"- **{k}:** {v}" for k, v in m.IMPLEMENTATION]
out += ["", "## Indicators", ""]
for i in m.INDICATORS:
    out += [f"### {i['name']}", "", f"*Data type: {i['category']}*", "",
            f"- **Formula:** {i['formula']}", f"- **Source dataset:** {i['dataset']}",
            f"- **Native spatial resolution:** {i['resolution']}", f"- **Temporal resolution:** {i['temporal']}",
            f"- **Reference period:** {i['reference']}", f"- **Quality filtering:** {i['quality']}",
            f"- **Processing method:** {i['method']}", f"- **Limitations:** {i['limitations']}", ""]
open(os.path.join(root, "docs", "METHODOLOGY.md"), "w", encoding="utf-8").write("\n".join(out))
print("wrote docs/METHODOLOGY.md")
