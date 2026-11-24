"""Decode a single Sina daily response in a disposable process."""

import json
import sys


def main() -> None:
    from akshare.stock.cons import hk_js_decode, zh_js_decode
    from py_mini_racer import MiniRacer

    market, encoded = json.load(sys.stdin)
    with MiniRacer() as decoder:
        decoder.eval(hk_js_decode if market == "HK" else zh_js_decode, timeout=3000)
        rows = decoder.call("d", encoded, timeout=3000)
    json.dump(rows, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
