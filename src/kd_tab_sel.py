# -*- coding: utf-8 -*-
"""★ 金蝶「主标签栏选择器」单一真相源（Single Source of Truth）。

背景（2026-10-27 根治 SF-51 反复复发）：
  金蝶 SPA 的**主标签栏**（顶部那一排「销售出库单列表 / 应收单-新增 / 凭证-修改 …」）
  在 DOM 里的专属 class 是 **`ul.kdMainTabsHeaderExtend`**。

  它同时**也**带着通用 class `ul.k-tabstrip-items`，而整页有 **14 个** `ul.k-tabstrip-items`
  （另外 13 个是各单据表单内部的页组，如「财务信息/物流信息/明细」）。于是：

    · 用 `ul.k-tabstrip-items`（或 `.k-tabstrip-items`）去 `filter(vis)[0]` 会**命中第一个可见的
      表单页组**，而不是主标签栏 —— 孤儿标签的 `li` 永远 `querySelector('#'+id)` 不到；
    · 主栏的数据还绑在**金蝶自研 widget `kendomainTabControl`** 上（不是 `kendoTabStrip`），
      所以 `data('kendoTabStrip')` 也必然落空。

  两个错叠一起 = `ctl.remove($(li))` 永远摘不掉孤儿标签 ⇒ 页签配额只增不减 ⇒ 攒到 15 爆掉。

  历史教训：这个结论曾在 skill 侧（kd_stray.py）修对过，但项目侧 `steps.py` 等多份代码
  各自**抄了旧选择器**，改一边漏一边，于是「上次解决了、这次又犯」。为此把正确写法
  收敛到本模块唯一一处，生产代码一律引用它，不再各自手写。

用法：
    from kd_tab_sel import MAIN_TAB_SELECTOR
    js = f'''() => {{ const strip = document.querySelector('{MAIN_TAB_SELECTOR}'); ... }}'''
"""

# 主标签栏的正确选择器（唯一真相）。带 `ul.` 前缀的完整 class，避免误命中表单页组。
MAIN_TAB_SELECTOR = "ul.kdMainTabsHeaderExtend"

# 主标签栏 widget 的 data() 键名（金蝶自研 Kendo TabStrip 子类，**不是** kendoTabStrip）。
# 释放配额/摘孤儿标签的唯一入口 = `$(root).data(WIDGET_KEY).remove($(li))`。
WIDGET_KEY = "kendomainTabControl"

# 兜底：主栏没找到时（极少数旧版本/异常场景）才退回通用 class。**不要**直接用它当首选。
# 用 document.querySelector 链式兜底，命中优先级：专属 class > 通用 class。
MAIN_TAB_JS = (
    "document.querySelector('ul.kdMainTabsHeaderExtend')"
    " || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0]"
    " || Array.from(document.querySelectorAll('.k-tabstrip-items')).filter(vis)[0]"
)

# 一条通用的「拿到主标签栏 widget 控制器」的 JS 片段（无 `vis` 依赖，可独立求值）。
# 用法：把 __MAIN_TAB__ / __WIDGET_KEY__ 替换后，`$(strip).closest('.k-widget')` 的 data() 即 ctl。
GET_CTL_JS = (
    "const $ = window.jQuery || window.$;"
    "const strip = document.querySelector('__MAIN_TAB__');"
    "if (!strip || !$) return null;"
    "const root = $(strip).closest('.k-widget')[0];"
    "const ctl = root ? $(root).data('__WIDGET_KEY__') : null;"
)
