# -*- coding: utf-8 -*-
"""
金蝶服务端反馈监听（kdsvc）
---------------------------
金蝶网页端所有动作（保存/提交/审核/下推/校验）都走同一个 POST：

    /k3cloud/Kingdee.BOS.ServiceFacade.ServicesStub.DynamicForm.DynamicFormService.Call.common.kdsvc

请求体里的 ap0/ap1/ap2/ap3 是 **gzip + base64(+urlencode)**；
响应体同理。解码后是 JSON 数组，元素形如：
    {"actionname": "showConfirm", "params": [{"msg": "...保存成功！"}]}
    {"actionname": "setFormTitle", "params": [[{"Key":2052,"Value":"销售增值税专用发票 - 修改"}]]}
    {"actionname": "UpdateMainMenuStates", "params": [{"key":"tbSplitSave","disabled":false,...}]}

为什么必须看它：金蝶的「保存成功」提示一闪而过（甚至截图都抓不到），
只看界面/截图**无法判断动作是否真的生效**（真实教训：2026-09-18 保存/提交一直"没反应"，
实际是点到了隐藏页面的按钮，且靠截图完全看不出来）。用这个监听能拿到服务端原文。

用法：
    kd = KdNet(page, log)
    kd.clear()
    ui.click_menu("保存")
    kd.wait_idle()
    for m in kd.messages(): print(m)      # 含保存成功/校验失败原文
    if kd.has("保存成功"): ...
"""
import base64
import gzip
import json
import urllib.parse


KD_SVC = "kdsvc"


def kd_decode(s):
    """金蝶 ap0/ap1/ap2/响应的 gzip+base64(+urlencode) → 明文，解不开就原样返回"""
    if not s:
        return ""
    try:
        raw = base64.b64decode(urllib.parse.unquote(s))
        return gzip.decompress(raw).decode("utf-8", "replace")
    except Exception:
        return str(s)


class KdNet:
    """挂在 page 上，收集金蝶服务端返回的所有 action"""

    def __init__(self, page, log=None):
        self.page = page
        self.log = log
        self._msgs = []          # 人可读的提示（showConfirm / showMessage 等）
        self._actions = []       # 原始 action 名
        self._raw = []           # 解码后的响应原文
        self._reqs = []
        page.on("request", self._on_req)
        page.on("response", self._on_resp)

    # ---------- 事件 ----------
    def _on_req(self, req):
        if KD_SVC not in req.url.lower():
            return
        d = {}
        try:
            d = json.loads(req.post_data or "{}")
        except Exception:
            pass
        self._reqs.append({
            "svc": kd_decode(d.get("ap0", ""))[:120],
            "form": kd_decode(d.get("ap1", ""))[:120],
            "arg": kd_decode(d.get("ap2", ""))[:900],
        })

    def _on_resp(self, resp):
        if KD_SVC not in resp.url.lower():
            return
        try:
            body = kd_decode(resp.text())
        except Exception:
            return
        self._raw.append(body)
        try:
            arr = json.loads(body)
        except Exception:
            return
        if not isinstance(arr, list):
            return
        for a in arr:
            if not isinstance(a, dict):
                continue
            name = a.get("actionname") or ""
            self._actions.append(name)
            if name in ("showConfirm", "showMessage", "showError", "alert"):
                for prm in (a.get("params") or []):
                    if isinstance(prm, dict) and prm.get("msg"):
                        self._msgs.append(prm["msg"])

    # ---------- 查询 ----------
    def clear(self):
        self._msgs.clear(); self._actions.clear(); self._raw.clear(); self._reqs.clear()

    def messages(self):
        return list(self._msgs)

    def actions(self):
        return list(self._actions)

    def requests(self):
        return list(self._reqs)

    def raw(self):
        return list(self._raw)

    def has(self, *keywords):
        txt = " ".join(self._msgs)
        return all(k in txt for k in keywords)

    def dump(self, log=None):
        """打印本次动作的服务端反馈（调试/留痕用）"""
        lg = log or self.log
        if not lg:
            return
        for r in self._reqs:
            lg.info(f"  → 调用 {r['svc']} {r['arg'][:120]}")
        if self._msgs:
            for m in self._msgs:
                if any(k in m for k in ("成功", "已提交", "已审核")):
                    lg.ok(f"  服务端：{m}")
                else:
                    lg.warn(f"  服务端：{m}")
        else:
            lg.warn(f"  服务端无提示消息（actions={self._actions[:12]}）")

    def wait_idle(self, ms=2000):
        """动作后等网络安静（金蝶没有请求完成事件，固定等最稳）"""
        self.page.wait_for_timeout(ms)


# ---------- 常用判定 ----------
OK_WORDS = ("成功", "已提交", "已审核")
BAD_WORDS = ("失败", "不能为空", "不允许", "错误", "未通过", "校验不通过", "重复")


def verdict(kd: KdNet):
    """
    根据服务端消息给一个结论：'ok' / 'bad' / 'unknown'
    """
    txt = " ".join(kd.messages())
    if not txt:
        return "unknown", ""
    if any(w in txt for w in BAD_WORDS):
        return "bad", txt
    if any(w in txt for w in OK_WORDS):
        return "ok", txt
    return "unknown", txt
