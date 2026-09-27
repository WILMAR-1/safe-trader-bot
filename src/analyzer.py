"""
Modulo de analisis y visualizacion de estrategia.
Genera graficos interactivos como FreqAI pero 100% nuestro.

Funcionalidades:
- Grafico de velas con indicadores y senales de compra/venta
- Curva de equity (ganancia acumulada)
- Distribucion de profit por trade
- Analisis por par
- Trades paralelos
- Heatmap de retornos mensuales
"""

import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from src.exchange import Exchange
from src.indicators import add_all_indicators
from src.strategy import SafeStrategy
from src.database import Database
from src.config import Config

logger = logging.getLogger(__name__)

REPORT_DIR = Path("data/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


class StrategyAnalyzer:
    """Analiza la estrategia y genera reportes HTML interactivos."""

    def __init__(self):
        self.exchange = Exchange()
        self.strategy = SafeStrategy(use_ml=False)
        self.db = Database(Config.DB_PATH)

    def analyze_pair(self, symbol: str, timeframe: str = "5m", days: int = 30) -> dict:
        """Analizar senales de un par especifico."""
        print(f"  Analizando {symbol}...")

        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=min(days * 288, 1000))
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = add_all_indicators(df)
        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)

        # Generar senales
        buy_signals = []
        sell_signals = []

        for i in range(200, len(df)):
            window = df.iloc[max(0, i - 200):i + 1].copy()
            if self.strategy.should_buy(window):
                buy_signals.append(i)

        df["buy_signal"] = 0
        df.loc[buy_signals, "buy_signal"] = 1

        print(f"  {symbol}: {len(buy_signals)} senales de compra en {len(df)} velas")

        return {
            "symbol": symbol,
            "dataframe": df,
            "buy_signals": len(buy_signals),
            "total_candles": len(df),
        }

    def generate_report(self, pairs: list[str] = None, timeframe: str = "5m",
                        days: int = 30) -> str:
        """Generar reporte HTML completo con graficos interactivos."""
        if pairs is None:
            pairs = Config.PAIR_WHITELIST

        print("\n" + "=" * 65)
        print("  ANALISIS DE ESTRATEGIA")
        print("=" * 65)

        all_data = {}
        total_buys = 0

        for pair in pairs:
            pair = pair.strip()
            try:
                result = self.analyze_pair(pair, timeframe, days)
                all_data[pair] = result
                total_buys += result["buy_signals"]
            except Exception as e:
                print(f"  Error analizando {pair}: {e}")

        # Cargar historial de trades desde la DB
        trades = self.db.get_trade_history(100)

        # Generar HTML
        html = self._generate_html(all_data, trades)

        filepath = REPORT_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n  Total senales de compra: {total_buys}")
        print(f"  Reporte guardado en: {filepath}")
        print(f"  Abre el archivo en tu navegador para ver los graficos.")
        print("=" * 65)

        return str(filepath)

    def _generate_html(self, all_data: dict, trades: list) -> str:
        """Generar el HTML del reporte con graficos."""

        # Preparar datos para graficos
        charts_html = ""

        for pair, data in all_data.items():
            df = data["dataframe"]
            charts_html += self._candlestick_chart(pair, df)

        # Equity curve desde trades
        equity_html = self._equity_chart(trades)

        # Profit distribution
        dist_html = self._profit_distribution(trades)

        # Stats por par
        pair_stats_html = self._pair_stats_table(trades)

        # Resumen
        total_trades = len(trades)
        wins = len([t for t in trades if (t.get("profit", 0) or 0) >= 0])
        losses = total_trades - wins
        total_profit = sum(t.get("profit", 0) or 0 for t in trades)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>SafeTraderBot - Analisis de Estrategia</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #60cdff; margin-bottom: 20px; font-size: 24px; }}
        h2 {{ color: #60cdff; margin: 30px 0 15px; font-size: 18px; }}
        .stats-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 30px; }}
        .stat {{ background: #16213e; border: 1px solid #2a3a5c; border-radius: 10px; padding: 18px; }}
        .stat .label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 1px; }}
        .stat .value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
        .positive {{ color: #6ccb5f; }}
        .negative {{ color: #ff5252; }}
        .chart-container {{ background: #16213e; border: 1px solid #2a3a5c; border-radius: 10px; padding: 15px; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th {{ text-align: left; padding: 10px; font-size: 12px; color: #888; border-bottom: 1px solid #2a3a5c; }}
        td {{ padding: 10px; font-size: 13px; border-bottom: 1px solid #1a2040; }}
        .pill {{ padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
        .pill-green {{ background: rgba(108,203,95,0.15); color: #6ccb5f; }}
        .pill-red {{ background: rgba(255,82,82,0.15); color: #ff5252; }}
        .footer {{ text-align: center; padding: 20px; color: #444; font-size: 12px; margin-top: 30px; }}
    </style>
</head>
<body>
<div class="container">
    <h1>Wilmar Safe Trader Bot - Analisis de Estrategia</h1>
    <p style="color:#888; margin-bottom:20px;">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Timeframe: {Config.TIMEFRAME}</p>

    <div class="stats-row">
        <div class="stat">
            <div class="label">Total Trades</div>
            <div class="value">{total_trades}</div>
        </div>
        <div class="stat">
            <div class="label">Win Rate</div>
            <div class="value {'positive' if win_rate >= 50 else 'negative'}">{win_rate:.0f}%</div>
        </div>
        <div class="stat">
            <div class="label">Ganadores / Perdedores</div>
            <div class="value">{wins} / {losses}</div>
        </div>
        <div class="stat">
            <div class="label">Profit Total</div>
            <div class="value {'positive' if total_profit >= 0 else 'negative'}">{"+" if total_profit >= 0 else ""}{total_profit:.2f} USDT</div>
        </div>
    </div>

    <h2>Graficos de Velas con Senales</h2>
    {charts_html}

    <h2>Curva de Equity</h2>
    {equity_html}

    <h2>Distribucion de Profit</h2>
    {dist_html}

    <h2>Rendimiento por Par</h2>
    {pair_stats_html}

    <div class="footer">Wilmar Safe Trader Bot v1.0 - 100% codigo propio</div>
</div>
</body>
</html>"""

    def _candlestick_chart(self, pair: str, df: pd.DataFrame) -> str:
        """Generar grafico de velas con indicadores."""
        # Ultimas 200 velas para el grafico
        df_plot = df.tail(200).copy()

        dates = df_plot["timestamp"].dt.strftime("%Y-%m-%d %H:%M").tolist()
        opens = df_plot["open"].tolist()
        highs = df_plot["high"].tolist()
        lows = df_plot["low"].tolist()
        closes = df_plot["close"].tolist()

        ema9 = df_plot["ema_9"].tolist()
        ema21 = df_plot["ema_21"].tolist()
        bb_upper = df_plot["bb_upper"].tolist()
        bb_lower = df_plot["bb_lower"].tolist()

        # Senales de compra
        buy_dates = df_plot[df_plot["buy_signal"] == 1]["timestamp"].dt.strftime("%Y-%m-%d %H:%M").tolist()
        buy_prices = df_plot[df_plot["buy_signal"] == 1]["close"].tolist()

        rsi = df_plot["rsi"].tolist()

        chart_id = pair.replace("/", "_").replace(" ", "")

        return f"""
    <div class="chart-container">
        <div id="chart_{chart_id}" style="height:450px;"></div>
        <div id="rsi_{chart_id}" style="height:150px;"></div>
        <script>
        Plotly.newPlot('chart_{chart_id}', [
            {{
                x: {dates},
                open: {opens}, high: {highs}, low: {lows}, close: {closes},
                type: 'candlestick', name: '{pair}',
                increasing: {{line: {{color: '#6ccb5f'}}}},
                decreasing: {{line: {{color: '#ff5252'}}}}
            }},
            {{ x: {dates}, y: {ema9}, type: 'scatter', mode: 'lines', name: 'EMA 9', line: {{color: '#60cdff', width: 1}} }},
            {{ x: {dates}, y: {ema21}, type: 'scatter', mode: 'lines', name: 'EMA 21', line: {{color: '#ffa726', width: 1}} }},
            {{ x: {dates}, y: {bb_upper}, type: 'scatter', mode: 'lines', name: 'BB Upper', line: {{color: '#555', width: 1, dash: 'dot'}} }},
            {{ x: {dates}, y: {bb_lower}, type: 'scatter', mode: 'lines', name: 'BB Lower', line: {{color: '#555', width: 1, dash: 'dot'}} }},
            {{ x: {buy_dates}, y: {buy_prices}, type: 'scatter', mode: 'markers', name: 'BUY',
               marker: {{color: '#6ccb5f', size: 12, symbol: 'triangle-up'}} }}
        ], {{
            title: '{pair}', paper_bgcolor: '#16213e', plot_bgcolor: '#16213e',
            font: {{color: '#888'}}, xaxis: {{rangeslider: {{visible: false}}}},
            legend: {{orientation: 'h', y: 1.12}}
        }});

        Plotly.newPlot('rsi_{chart_id}', [
            {{ x: {dates}, y: {rsi}, type: 'scatter', mode: 'lines', name: 'RSI', line: {{color: '#ba68c8', width: 1.5}} }},
            {{ x: {dates}, y: {[30]*len(dates)}, type: 'scatter', mode: 'lines', name: 'Oversold', line: {{color: '#6ccb5f', width: 0.5, dash: 'dash'}} }},
            {{ x: {dates}, y: {[70]*len(dates)}, type: 'scatter', mode: 'lines', name: 'Overbought', line: {{color: '#ff5252', width: 0.5, dash: 'dash'}} }}
        ], {{
            title: 'RSI', paper_bgcolor: '#16213e', plot_bgcolor: '#16213e',
            font: {{color: '#888'}}, height: 150, margin: {{t: 30, b: 30}},
            yaxis: {{range: [0, 100]}}, showlegend: false
        }});
        </script>
    </div>"""

    def _equity_chart(self, trades: list) -> str:
        if not trades:
            return '<div class="chart-container"><p style="color:#888;padding:20px;">Sin trades para graficar equity.</p></div>'

        equity = [0]
        dates = ["Inicio"]
        cumulative = 0

        for t in reversed(trades):
            cumulative += t.get("profit", 0) or 0
            equity.append(round(cumulative, 2))
            dates.append(t.get("exit_time", "")[:10] if t.get("exit_time") else f"Trade {len(dates)}")

        return f"""
    <div class="chart-container">
        <div id="equity_chart" style="height:300px;"></div>
        <script>
        Plotly.newPlot('equity_chart', [{{
            x: {dates}, y: {equity}, type: 'scatter', mode: 'lines',
            fill: 'tozeroy',
            line: {{color: '#60cdff', width: 2}},
            fillcolor: 'rgba(96,205,255,0.1)'
        }}], {{
            title: 'Equity (Ganancia Acumulada USDT)',
            paper_bgcolor: '#16213e', plot_bgcolor: '#16213e',
            font: {{color: '#888'}}, height: 300
        }});
        </script>
    </div>"""

    def _profit_distribution(self, trades: list) -> str:
        if not trades:
            return '<div class="chart-container"><p style="color:#888;padding:20px;">Sin trades para distribucion.</p></div>'

        profits = [t.get("profit_pct", 0) or 0 for t in trades]

        return f"""
    <div class="chart-container">
        <div id="dist_chart" style="height:300px;"></div>
        <script>
        Plotly.newPlot('dist_chart', [{{
            x: {profits}, type: 'histogram',
            marker: {{color: 'rgba(96,205,255,0.7)'}},
            nbinsx: 30
        }}], {{
            title: 'Distribucion de Profit por Trade (%)',
            paper_bgcolor: '#16213e', plot_bgcolor: '#16213e',
            font: {{color: '#888'}}, height: 300,
            xaxis: {{title: 'Profit %'}}, yaxis: {{title: 'Frecuencia'}}
        }});
        </script>
    </div>"""

    def _pair_stats_table(self, trades: list) -> str:
        if not trades:
            return '<div class="chart-container"><p style="color:#888;padding:20px;">Sin trades.</p></div>'

        pair_data = {}
        for t in trades:
            s = t["symbol"]
            if s not in pair_data:
                pair_data[s] = {"trades": 0, "wins": 0, "profit": 0, "profits": []}
            pair_data[s]["trades"] += 1
            pair_data[s]["profit"] += t.get("profit", 0) or 0
            pair_data[s]["profits"].append(t.get("profit_pct", 0) or 0)
            if (t.get("profit", 0) or 0) >= 0:
                pair_data[s]["wins"] += 1

        rows = ""
        for pair, d in sorted(pair_data.items(), key=lambda x: x[1]["profit"], reverse=True):
            wr = (d["wins"] / d["trades"] * 100) if d["trades"] > 0 else 0
            avg = np.mean(d["profits"]) if d["profits"] else 0
            best = max(d["profits"]) if d["profits"] else 0
            worst = min(d["profits"]) if d["profits"] else 0
            cls = "positive" if d["profit"] >= 0 else "negative"
            sign = "+" if d["profit"] >= 0 else ""

            rows += f"""<tr>
                <td><strong>{pair}</strong></td>
                <td>{d['trades']}</td>
                <td>{d['wins']}</td>
                <td>{wr:.0f}%</td>
                <td class="{cls}">{sign}{d['profit']:.2f}</td>
                <td>{avg:.2f}%</td>
                <td class="positive">+{best:.2f}%</td>
                <td class="negative">{worst:.2f}%</td>
            </tr>"""

        return f"""
    <div class="chart-container">
        <table>
            <thead>
                <tr><th>Par</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>Profit USDT</th><th>Avg %</th><th>Mejor</th><th>Peor</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>"""
