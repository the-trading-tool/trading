helptext_general = """
<style type="text/css">
table, th, td {
  border: 1px solid black;
  border-collapse: collapse;
}
th, td {
  padding: 10px;
}
th {
  text-align: left;
}</style>
<h3>How assets are selected:</h3>
</br>
</br>
This Tradingtool supports the evaluation of the performance of equity investments. The tool can use any trend indicator such as macd, rsi, cci, adx, momentum, ewo or their historical performance such as roa, ebitgrowth, margingrowth, dividend rate indicators to forecast inherent value.
</br>
The weighted sum of these selected indicators forms my own trading tools indicator, which I call overallValueTrend.
</br>
This indicator identifies the best performing stocks at the moment and is dependent on the set of indicators used to calculate the Ovt indicator.
</br>

</br>
This overall value trend indicator ranges from of -100 (lowest performance) to +100 (best performance).
</br>

</br>
To check how well this new indicator works, I ran several backtests with this evaluation model and found an annualized performance of at least 25% since 2019. This certainly outperforms any underlying index.
</br>
Thus, the loss to profit ratio is often better than 1:2 except of any specific trading costs which are specific to your trader.
</br>

</br>
Please note that this indicator is yet not tested in all markets. But you may find your own setup for the market you like to trade.
</br>

</br>
Use this indicator as a support for defining your own strategy, ideally combining the trading signals of the ovt indicator with other overlay or oscillator indicators.
</br>
By combining other indicators with ovt, false positive signals can be avoided. After analysis, the buy signal overallValueTrend should be at least 15 to 20 points above the index average.
</br>
In my strategy, I sell assets when the ewo (Elliot Wave Indicator) is lower than its ema (with a period of 9). In my setup I currently use assets from SPX, DJI, GDAX, MDAX, SDAX, HSI for trades. This weighs the risk and achieves a good profit-loss ratio. I select 3 to a maximum of 8 stocks per index, set the stop loss at half of the average vola, adjust the stop loss daily and invest in as many indices as possible to achieve a balanced risk/reward profile. In this way, I trade a portfolio of up to 40 stocks.
</br>

</br>
The overallValueTrend indicator also uses several “non-standardized” valuation criteria, one of which, for example, uses machine learning to predict future prices and is based on xgboost, a machine learning tool.
</br>

</br>
Currently, all the data for analysis comes from finance.yahoo.com.
</br>
</br>
Even though the data used is provided with a certain time delay, it fits perfectly with the investment strategy as I am not interested in day-trading trading. The strategy is an opportunity for people who like to participate in the stock market and want to win with just a few minutes of their daily free time.
</br>
Essentially, all the trading suggestions that result from my model focus on a short-term investment horizon, ranging from a few days to a few weeks.
</br>
My stop loss is usually -3% of the buy price.
</br>
</br>
Translated with www.DeepL.com/Translator (free version)
</br>
Let's dive a little into the valuation parameters:</br>
</br>
For better structuring, the following table provides an overview of these indicators, the required performance per "sub" criterion and the weighting for calculation as well as a brief description.</br><br>
<table width=80%>  
<tr><th>Evaluation</th><th>Value points</th><th>Value weight</th><th>Value range</th><th>Comment</th></tr>
<tr><td>daily, weekly and monthly trend</td><td>3</td><td>3</td><td> </td><td>if trend is rising for all 3 we rate performance</td></tr>
<tr><td>trendDirection</td><td> 1</td><td> 3</td><td> -1 to 2</td><td>trendDirection is an own indicator and uses adx, momentum, rsi and close price</td></tr>
<tr><td>dTrend wkTrend moTrend</td><td></td><td></td><td>-1 to 1</td><td> (-100 to 100 percent), an indicator based on daily weekly and monthly percentage changes</td></tr>
<tr><td>vola / 2 < moTrend</td><td> 1</td><td> 1</td><td> </td><td> in case asset vola half is less than monthly trend pct change</td></tr>
<tr><td>vola &lt; moTrend</td><td> 1</td><td> 1</td><td> </td><td> in case asset vola is less than monthly trend change. Note this comes in additon to the former.</td></tr>
<tr><td>macdTrend > 1</td><td> 1</td><td> 1</td><td> </td><td> tests if macdTrend is currently rising or falling</td></tr>
<tr><td>roa &gt; 2%</td><td> 1</td><td> 1</td><td> </td><td> roa (Return on Asset) shall be above 2%</td></tr>
<tr><td>roa &lt; 5%</td><td> 0</td><td> -1</td><td></td><td> we just reduce the weighting for our overall Trend by 1 in case roa is less 5%</td></tr>
<tr><td>logVola &lt; 10%</td><td> 1</td><td> 1</td><td></td><td> considerd positive if logVola is lower 10%</td></tr>
<tr><td>forwardEps &gt; 0.1</td><td> 0.5</td><td> 0.5</td><td></td><td> forward earning per share shall be greater than 0.1</td></tr>                 
<tr><td>forwardPE &gt; 0 and &lt; 25</td><td> 0.5</td><td> 0.5</td><td></td><td> in case we don't have a forwardPE value we use the trailingPE</td></tr>
<tr><td>pctTargetHighPrice &gt; 10%</td><td> 1</td><td> 0.5</td><td></td><td> evaluates how far the current close price can grow until reaches  of analysts opinon targetHighPrice rating.</td></tr>
<tr><td>pctTargetHighPrice &gt; 20%</td><td> 0</td><td> 0.5</td><td></td><td> is the difference targetHighPrice of analysts opinon ratings more than 20% above current close price we add another 0.5 to the points but not change weighting</td></tr>
<tr><td>pctTargetHighPrice &lt; 0%</td><td> 0</td><td> -1</td><td></td><td> is the difference targetHighPrice of analysts opinon ratings compared less than 0 we only reduce points by 1 and not change weighting</td></tr>
<tr><td>dividentRate &gt; 0</td><td> 0.5</td><td> 0.5</td><td></td><td> we recognize if the company pays a dividend</td></tr>
<tr><td>earningsGrowth &gt; 0</td><td> 0.5</td><td> 0.5</td><td></td><td> we positivly recognize if the company earnings grow</td></tr>
<tr><td>operatingMargins &gt; 0</td><td> 0.5</td><td> 0.5</td><td></td><td> we recognize if the company operating margins are positive</td></tr>
<tr><td>priceToBook &gt; 50</td><td> 1</td><td> 1</td><td></td><td> we value a high priceToBook value as a buyer interest in a company.</td></tr>
<tr><td>priceToBook &lt; 5</td><td> 0</td><td> 0.5</td><td></td><td> but we also value if an asset might be undervalued but don't change weighting</td></tr> 
<tr><td>priceToBook &lt; 0</td><td> 0</td><td> 0.5</td><td></td><td> and we also value if an asset might be undervalued but don't change weighting which means we in general take a look at both siedes of the medal. Combined with the other indicators the potential risk is leveraged well as we do not want to invest in a risky company for long period. Mind our investment horizont is days to few weeks.</td></tr>
<tr><td>enterpriseValue ./. totalDebt > 3</td><td>1</td><td>1</td><td></td><td> we consider companies with low debt </td></tr>
<tr><td>averageVolume &gt; 1000000</td><td>1 </td><td>0.5</td><td></td><td>we are also more interested in companies with a high average Volume traded.</td></tr>
<tr><td>averageVolume &gt; 10000000</td><td>0 </td><td>0.5</td><td></td><td></td></tr>
<tr><td>rsi &gt; 20 &lt; 80</td><td> 0.5</td><td> 0.5</td><td></td><td></td></tr>
<tr><td>sharpe &gt; 1</td><td> 0.5</td><td>0.5</td><td></td><td>a positive sharpe ratio is also valued by our model.</td></tr>
<tr><td>sharpe &gt; 2</td><td> 0.5</td><td>0.5</td><td></td><td></td></tr>
<tr><td>predictedHigh &lt; low</td><td> 0.5</td><td> 0.5</td><td></td><td>based on our machine learning indicator we value assets low price is ranging above predicted High price. This to us means a high market attention and buyer interest in this asset.</td></tr>
<tr><td>priceTrend &lt; low</td><td>2</td><td>-2</td><td></td><td> in case price is falling</td></tr>
<tr><td>priceTrend &gt; high</td><td>0,2</td><td></td><td></td><td> in case price is rising</td></tr>
</table>
"""