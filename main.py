"""
SafeTraderBot - Punto de entrada principal.
Bot de trading 100% propio. Sin dependencias de terceros que puedan robar datos.

Comandos:
  python main.py              -> Ejecutar bot (trading)
  python main.py backtest     -> Backtesting contra datos historicos
  python main.py optimize     -> Optimizar parametros de la estrategia
"""

import sys
import logging
import pandas as pd
from pathlib import Path
from src.config import Config

Path("data").mkdir(exist_ok=True)

# Configurar logging
log_level = logging.WARNING if len(sys.argv) > 1 and sys.argv[1] in ("backtest", "optimize") else getattr(logging, Config.LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("ccxt").setLevel(logging.WARNING)


BANNER = r"""
$$\      $$\ $$$$$$\ $$\       $$\      $$\  $$$$$$\  $$$$$$$\         $$$$$$\   $$$$$$\  $$$$$$$$\ $$$$$$$$\       $$$$$$$$\ $$$$$$$\   $$$$$$\  $$$$$$$\  $$$$$$$$\ $$$$$$$\        $$$$$$$\   $$$$$$\ $$$$$$$$\
$$ | $\  $$ |\_$$  _|$$ |      $$$\    $$$ |$$  __$$\ $$  __$$\       $$  __$$\ $$  __$$\ $$  _____|$$  _____|      \__$$  __|$$  __$$\ $$  __$$\ $$  __$$\ $$  _____|$$  __$$\       $$  __$$\ $$  __$$\\__$$  __|
$$ |$$$\ $$ |  $$ |  $$ |      $$$$\  $$$$ |$$ /  $$ |$$ |  $$ |      $$ /  \__|$$ /  $$ |$$ |      $$ |               $$ |   $$ |  $$ |$$ /  $$ |$$ |  $$ |$$ |      $$ |  $$ |      $$ |  $$ |$$ /  $$ |  $$ |
$$ $$ $$\$$ |  $$ |  $$ |      $$\$$\$$ $$ |$$$$$$$$ |$$$$$$$  |      \$$$$$$\  $$$$$$$$ |$$$$$\    $$$$$\             $$ |   $$$$$$$  |$$$$$$$$ |$$ |  $$ |$$$$$\    $$$$$$$  |      $$$$$$$\ |$$ |  $$ |  $$ |
$$$$  _$$$$ |  $$ |  $$ |      $$ \$$$  $$ |$$  __$$ |$$  __$$<        \____$$\ $$  __$$ |$$  __|   $$  __|            $$ |   $$  __$$< $$  __$$ |$$ |  $$ |$$  __|   $$  __$$<       $$  __$$\ $$ |  $$ |  $$ |
$$$  / \$$$ |  $$ |  $$ |      $$ |\$  /$$ |$$ |  $$ |$$ |  $$ |      $$\   $$ |$$ |  $$ |$$ |      $$ |               $$ |   $$ |  $$ |$$ |  $$ |$$ |  $$ |$$ |      $$ |  $$ |      $$ |  $$ |$$ |  $$ |  $$ |
$$  /   \$$ |$$$$$$\ $$$$$$$$\ $$ | \_/ $$ |$$ |  $$ |$$ |  $$ |      \$$$$$$  |$$ |  $$ |$$ |      $$$$$$$$\          $$ |   $$ |  $$ |$$ |  $$ |$$$$$$$  |$$$$$$$$\ $$ |  $$ |      $$$$$$$  | $$$$$$  |  $$ |
\__/     \__|\______|\________|\__|     \__|\__|  \__|\__|  \__|       \______/ \__|  \__|\__|      \________|         \__|   \__|  \__|\__|  \__|\_______/ \________|\__|  \__|      \_______/  \______/   \__|
"""


def cmd_trade():
    """Ejecutar el bot de trading."""
    try:
        from src.bot import TradingBot
        bot = TradingBot()
        bot.run()
    except ValueError as e:
        print(f"\nError de configuracion:\n{e}")
        print("\nCopia .env.example a .env y configura tus valores.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBot detenido por el usuario.")


def cmd_backtest():
    """Ejecutar backtesting."""
    from src.backtester import Backtester

    days = 30
    if len(sys.argv) > 2:
        try:
            days = int(sys.argv[2])
        except ValueError:
            pass

    bt = Backtester(
        initial_balance=1000.0,
        stake_amount=Config.STAKE_AMOUNT,
        commission=0.001,
        max_open_trades=Config.MAX_OPEN_TRADES,
    )

    result = bt.run(
        pairs=Config.PAIR_WHITELIST,
        timeframe=Config.TIMEFRAME,
        days=days,
    )

    print(result.summary())


def cmd_optimize():
    """Ejecutar optimizacion de parametros."""
    from src.optimizer import Optimizer

    trials = 200
    days = 30
    if len(sys.argv) > 2:
        try:
            trials = int(sys.argv[2])
        except ValueError:
            pass
    if len(sys.argv) > 3:
        try:
            days = int(sys.argv[3])
        except ValueError:
            pass

    opt = Optimizer(
        initial_balance=1000.0,
        stake_amount=Config.STAKE_AMOUNT,
        commission=0.001,
    )

    opt.optimize(
        pairs=Config.PAIR_WHITELIST,
        timeframe=Config.TIMEFRAME,
        days=days,
        max_trials=trials,
    )


def cmd_download():
    """Descargar datos historicos reales."""
    from src.data_downloader import DataDownloader

    days = 365
    if len(sys.argv) > 2:
        try:
            days = int(sys.argv[2])
        except ValueError:
            pass

    dl = DataDownloader()
    dl.download_all(
        pairs=Config.PAIR_WHITELIST,
        timeframe=Config.TIMEFRAME,
        days=days,
    )

    # Descargar tambien en 1h y 4h para analisis
    print("\n  Descargando timeframes adicionales para analisis...")
    dl.download_all(pairs=Config.PAIR_WHITELIST, timeframe="1h", days=days)
    dl.download_all(pairs=Config.PAIR_WHITELIST, timeframe="1d", days=days)


def cmd_train():
    """Entrenar modelo de Machine Learning."""
    from src.exchange import Exchange
    from src.indicators import add_all_indicators
    from src.ml_engine import MLEngine

    days = 30
    if len(sys.argv) > 2:
        try:
            days = int(sys.argv[2])
        except ValueError:
            pass

    print(f"\n  Entrenando modelo ML con {days} dias de datos...")

    exchange = Exchange()
    engine = MLEngine(model_type="randomforest", min_confidence=0.65)

    for pair in Config.PAIR_WHITELIST:
        pair = pair.strip()
        print(f"\n  --- {pair} ---")
        try:
            limit = min(days * (1440 // 5), 1000)  # 5m candles
            ohlcv = exchange.fetch_ohlcv(pair, Config.TIMEFRAME, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = add_all_indicators(df)
            df.dropna(inplace=True)

            result = engine.train(df, pair)
            if "error" not in result:
                print(f"  Train accuracy: {result['train_accuracy']:.1f}%")
                print(f"  Test accuracy:  {result['test_accuracy']:.1f}%")
                print(f"  Features:       {result['features']}")
                print(f"  Samples:        {result['train_samples']} train / {result['test_samples']} test")
                print(f"  Buy signals:    {result['buy_signal_ratio']:.1f}%")
                print(f"  Tiempo:         {result['train_time']:.1f}s")
            else:
                print(f"  Error: {result['error']}")
        except Exception as e:
            print(f"  Error: {e}")

    print("\n  Modelos guardados en data/models/")
    print("  El bot los cargara automaticamente al iniciar.")


def cmd_analyze():
    """Analizar la estrategia y generar reporte visual."""
    from src.analyzer import StrategyAnalyzer

    days = 30
    if len(sys.argv) > 2:
        try:
            days = int(sys.argv[2])
        except ValueError:
            pass

    analyzer = StrategyAnalyzer()
    filepath = analyzer.generate_report(
        pairs=Config.PAIR_WHITELIST,
        timeframe=Config.TIMEFRAME,
        days=days,
    )
    print(f"\n  Abre en tu navegador: {filepath}")


def cmd_data_stats():
    """Ver estadisticas de datos locales."""
    from src.data_downloader import DataDownloader
    dl = DataDownloader()
    dl.get_stats()


def main():
    print(BANNER)
    print("  v1.0 | 100% codigo propio | Sin telemetria | Sin recoleccion de datos")
    print()

    command = sys.argv[1] if len(sys.argv) > 1 else "trade"

    commands = {
        "trade": cmd_trade,
        "backtest": cmd_backtest,
        "optimize": cmd_optimize,
        "download": cmd_download,
        "train": cmd_train,
        "analyze": cmd_analyze,
        "data": cmd_data_stats,
    }

    if command in commands:
        commands[command]()
    else:
        print("  Comandos disponibles:")
        print("    python main.py              Ejecutar bot (trading en vivo/simulacion)")
        print("    python main.py backtest     Backtesting contra datos historicos")
        print("    python main.py backtest 60  Backtesting con 60 dias de datos")
        print("    python main.py optimize     Optimizar parametros de la estrategia")
        print("    python main.py optimize 300 Optimizar con 300 combinaciones")
        print("    python main.py train        Entrenar modelo ML (Machine Learning)")
        print("    python main.py train 60     Entrenar con 60 dias de datos")
        print("    python main.py analyze      Analisis visual con graficos interactivos")
        print("    python main.py analyze 60   Analisis con 60 dias de datos")
        print("    python main.py download     Descargar datos historicos (1 ano)")
        print("    python main.py download 180 Descargar datos historicos (180 dias)")
        print("    python main.py data         Ver datos locales disponibles")
        print()


if __name__ == "__main__":
    main()
