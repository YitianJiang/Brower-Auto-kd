"""核验「下推专票时有没有改票面数字」——行级多重集比对，只读离线数据。

用法：
    python src/check_iv_tamper.py [output/hz_diff_lines.json]

判据（比金额合计更硬）：
  把应收单明细行与专票明细行都看成 (物料名, 数量, 含税金额) 的多重集：
    · 票行 − 应收行  ≠ ∅  → **有行被改过**（票上出现了应收单里不存在的组合）
    · 应收行 − 票行  = 缺失的整行，其金额之和应精确等于「应收合计 − 票面合计」
  两条同时成立 = 只漏行、未改数。

背景：2026-11-01 用户质疑「推的过程有没有改增值税发票的数」。
杭州 10 月 101 张实测：93 张分毫不差，8 张差异 100% 等于缺失整行之和（69,836.75），
被改动行数 = 0。代码侧也确认 RPA 从不写专票明细（见 push_chain.step_invoice）。
"""
import json
import sys
from collections import Counter
from decimal import Decimal as D

Q = D("0.01")


def _k(mat, qty, amt):
    return (mat, str(D(str(qty)).quantize(Q)), str(D(str(amt)).quantize(Q)))


def main(path="output/hz_diff_lines.json"):
    data = json.load(open(path, encoding="utf-8"))
    tot_missing = D(0)
    tot_diff = D(0)
    all_ok = True
    print(f"数据：{path}  共 {len(data)} 张\n")
    for r in data:
        ar = Counter(_k(x["FMaterialId.FName"], x["FPriceQty"], x["FALLAMOUNTFOR_D"])
                     for x in r["ar_det"])
        iv = Counter(_k(x["FMaterialId.FName"], x["FPriceQty"], x["FAllAmount"])
                     for x in r["iv_det"])
        tampered = iv - ar            # 票上多出来的 = 被改过的行
        missing = ar - iv             # 应收有、票没有 = 漏掉的行
        miss_amt = sum((D(k[2]) for k in missing.elements()), D(0))
        diff = D(str(r["diff"]))
        ok = (not tampered) and (miss_amt == -diff)
        all_ok &= ok
        print(f"{r['ar']:<20} 应收{len(r['ar_det']):>3}行 / 票{len(r['iv_det']):>3}行   "
              f"漏{sum(missing.values()):>2}行={miss_amt:>11}  差额={diff:>11}   "
              f"被改动行={sum(tampered.values())}  {'OK' if ok else '** 有改数 **'}")
        for k, v in tampered.items():
            print(f"    !! 票上有应收单不存在的行：{k} ×{v}")
        tot_missing += miss_amt
        tot_diff += diff
    print(f"\n合计：漏行金额 {tot_missing}   差额合计 {tot_diff}   "
          f"{'全部吻合 → 只漏行、未改数' if all_ok else '** 存在改数，需人工核查 **'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "output/hz_diff_lines.json"))
