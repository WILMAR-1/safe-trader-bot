"""
Validacion walk-forward de TrendStrategy (seguimiento de tendencia).

Uso:
  python trend_backtest.py                 # 1h, pares de PAIR_WHITELIST, solo largos (spot)
  python trend_backtest.py 4h              # otro timeframe
  python trend_backtest.py 1h --short      # permitir cortos (Exness/CFD)

Necesita datos locales: python main.py download 1095   (3 anos recomendado)
Si solo hay velas de 5m, se agrupan al timeframe pedido.

SIMULACION. No ejecuta ordenes reales.
"""
import sys

from src.config import Config
from src.data_downloader import DataDownloader
from src.trend_backtest import Costs, resample, walk_forward


def load(pairs, timeframe):
    dl = DataDownloader()
    out = {}
    for p in pairs:
        df = dl.load_local(p, timeframe)
        if df.empty:
            base = dl.load_local(p, "5m")
            if not base.empty:
                df = resample(base, timeframe)
        if len(df) > 1000:
            out[p] = df
        else:
            print(f"  {p}: sin datos suficientes en {timeframe} (se omite)")
    return out


def fmt(s):
    return (f"retorno {s['total_return']:+.1%} | CAGR {s['cagr']:+.1%} | Sharpe {s['sharpe']:.2f} | "
            f"MaxDD {s['max_drawdown']:.1%} | Calmar {s['calmar']:.2f}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    timeframe = args[0] if args else "1h"
    allow_short = "--short" in sys.argv
    pairs = [p.strip() for p in Config.PAIR_WHITELIST if p.strip()]

    data = load(pairs, timeframe)
    if not data:
        print("No hay datos. Ejecuta primero: python main.py download 1095")
        return

    print("=" * 70)
    print(f"  TREND FOLLOWING - WALK-FORWARD ({timeframe}, {'largos+cortos' if allow_short else 'solo largos'})")
    print("=" * 70)
    r = walk_forward(data, timeframe, folds=4, allow_short=allow_short,
                     max_leverage=Config.MAX_LEVERAGE if allow_short else 1.0,
                     risk_pct=Config.RISK_PER_TRADE_PCT, costs=Costs())
    s, bh = r["oos"], r["buy_hold"]
    print(f"  Pares: {', '.join(data)} | {r['years_oos']:.1f} anos fuera de muestra")
    print(f"  Variantes probadas: {r['n_trials']} (se penalizan en el DSR)")
    print("  Parametros elegidos por tramo (solo con datos pasados):")
    for i, p in enumerate(r["chosen"], 1):
        print(f"    tramo {i}: canal={p.entry_n} trailing={p.trail_atr}xATR momentum={p.momentum_n}")
    print("-" * 70)
    print(f"  ESTRATEGIA (OOS): {fmt(s)}")
    print(f"                    trades {s['trades']} | win {s['win_rate']:.0%} | "
          f"PF {s['profit_factor']:.2f} | PSR {s['psr']:.0%} | DSR {s['dsr']:.0%}")
    print(f"  BUY & HOLD:       {fmt(bh)}")
    print("=" * 70)
    print("  VEREDICTO")
    if s["trades"] < 30:
        print("  Muy pocos trades fuera de muestra: no se puede concluir nada.")
    elif s["dsr"] >= 0.95 and s["profit_factor"] > 1.1:
        print("  PROMETEDOR: la ventaja sobrevive a costes y a la penalizacion por variantes.")
        print("  Siguiente paso: semanas en dry_run antes de arriesgar dinero.")
    elif s["total_return"] > 0 and s["max_drawdown"] < bh["max_drawdown"]:
        print("  DEFENSIVO: gana y cae menos que buy&hold, pero la ventaja no es estadisticamente")
        print("  concluyente (DSR < 95%). Valido como gestion de riesgo, no como maquina de dinero.")
    else:
        print("  NO APTO: fuera de muestra no demuestra ventaja. No lo uses con dinero real.")
    print("=" * 70)


if __name__ == "__main__":
    main()
