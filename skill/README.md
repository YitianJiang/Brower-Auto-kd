# skill/ —— 金蝶 RPA 技能包（跨项目通用层）

这个目录是 skill 包 `kingdee-k3cloud-browser-rpa` 的完整副本，随本仓库一起走 git，
换机器 clone 本仓库即得。

## 它和 `src/` 的关系（重要，避免再分裂）

| | `skill/`（这里） | `src/`（项目代码） |
|---|---|---|
| 定位 | 跨项目通用的「方法论 + 通用脚本库」 | 当前批次落地脚本 |
| 通用脚本 | `scripts/kd_*.py`（kd_stray/kd_core/kd_query/...） | 各自内联了一份 JS |
| 单一真相源 | 文档（SKILL.md / references/silent-failures.md） | `kd_tab_sel.py`（选择器唯一真相） |

**历史教训（SF-51 三次复发）**：这两层的「主标签栏选择器」知识曾经分裂——skill 修对了、
项目 `steps.py` 抄旧选择器，改一边漏一边 → 页签配额攒到 15 反复爆。

**现在的约定**：
- 页签选择器 / widget 名这类「唯一真相」，项目侧**只**在 `src/kd_tab_sel.py` 定义，
  项目代码一律 `from kd_tab_sel import MAIN_TAB_SELECTOR, WIDGET_KEY`，禁止手写。
- skill 侧文档（`references/silent-failures.md` SF-51）只记结论，不另维护一份可执行代码。
- 两边都改时，**必须同步**（或只改 `kd_tab_sel.py` 一处，skill 文档引用它）。

## 新机器怎么装这个 skill

clone 本仓库后，把 `skill/` 目录拷到用户级 skills 目录即可被 WorkBuddy 加载：

```bash
# Linux/macOS
mkdir -p ~/.workbuddy/skills
cp -r skill ~/.workbuddy/skills/kingdee-k3cloud-browser-rpa

# Windows（PowerShell）
New-Item -ItemType Directory -Force $HOME\.workbuddy\skills
Copy-Item -Recurse -Force skill $HOME\.workbuddy\skills\kingdee-k3cloud-browser-rpa
```

装好后，新机器上任何「金蝶 RPA」任务都会自动加载这个 skill。
