import streamlit as st
import requests
import re
import feedparser
import nltk
import pandas as pd
import plotly.express as px
from datetime import datetime

# Download vader_lexicon -- SSL bypass for restricted environments
try:
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass
nltk.download('vader_lexicon', quiet=True)

try:
    from nltk.sentiment import SentimentIntensityAnalyzer
    _SIA_AVAILABLE = True
except Exception:
    _SIA_AVAILABLE = False

@st.cache_data(ttl=300, show_spinner=False)
def _cached_fetch_news(ticker: str, url: str) -> list:
    """Fetch Yahoo Finance RSS feed; cached per ticker for 5 minutes."""
    feed = feedparser.parse(url)
    return [{'title': e.title, 'link': e.link, 'published': e.published} for e in feed.entries]


@st.cache_data(ttl=300, show_spinner=False)
def _cached_fetch_article_text(url: str):
    """Fetch article body text; cached per URL for 5 minutes."""
    try:
        response = requests.get(url, timeout=5)
        text = ' '.join(re.findall(r'<p>(.*?)</p>', response.text))
        return text if text else None
    except Exception:
        return None


class YahooNewsSentiment:

    def __init__(self, ticker, screen_region=st):
        """Set up the sentiment analyser for the given Yahoo Finance ticker."""
        self.ticker = ticker
        self.screen_region = screen_region
        self.url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
        if _SIA_AVAILABLE:
            try:
                self.sia = SentimentIntensityAnalyzer()
            except Exception:
                self.sia = None
        else:
            self.sia = None

    def fetch_news(self):
        """Parse the Yahoo Finance RSS feed and return a list of article dicts."""
        return _cached_fetch_news(self.ticker, self.url)

    def fetch_article_text(self, url):
        """Fetch raw article text by scraping paragraph tags from the URL."""
        return _cached_fetch_article_text(url)

    def analyze_sentiment(self, articles, single=False):
        """Score sentiment with VADER; single=True returns a score dict for one text string."""
        if self.sia is None:
            # Sentiment analysis not available (vader_lexicon missing)
            if single:
                return {'neg': 0.0, 'neu': 1.0, 'pos': 0.0, 'compound': 0.0}
            return articles
        if not single:
            for article in articles:
                text = self.fetch_article_text(article['link'])
                content = text if text else article['title']
                sentiment = self.sia.polarity_scores(content)
                article.update(sentiment)
            return articles
        else:
            return self.sia.polarity_scores(articles)

    def render(self):
        """Fetch news, analyze sentiment, and render a bar chart of average scores."""
        self.screen_region.subheader("Yahoo Finance News Sentiment Analysis")

        articles = self.fetch_news()
        self.list_articles(articles)
        analyzed_articles = self.analyze_sentiment(articles)
        df = pd.DataFrame(analyzed_articles)
        
        #self.screen_region.write(f"### Sentiment analysis for {self.ticker}")
        # 
        try:        
            fig = px.bar(
                df[['pos', 'neu', 'neg']].mean().reset_index(),
                x='index', 
                y=0, 
                color='index',
                labels={'index': 'Sentiment', 0: 'Wert'},
                title='Durchschnittliche Sentiment-Werte'
            )
            self.screen_region.plotly_chart(fig)
        except Exception:
            pass

    def list_articles(self, articles):
        """Render a table of news items sorted by publish date (newest first), with per-headline sentiment scores."""
        if not articles:
            return

        news_items = []
        for article in articles:
            sentiment = self.analyze_sentiment(article['title'], True)
            compound_score = sentiment['compound']

            if compound_score >= 0.05:
                sentiment_category = "Positive"
            elif compound_score <= -0.05:
                sentiment_category = "Negative"
            else:
                sentiment_category = "Neutral"

            try:
                published_dt = datetime.strptime(article['published'], "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                published_dt = datetime.min  # If date is missing, sort to the back

            news_items.append({
                'published_dt': published_dt,
                'Published': article['published'],
                'Title': article['title'],
                'Sentiment': sentiment_category,
                'Score': round(compound_score, 2),
                'Link': article['link'],
            })

        # Newest first
        news_items.sort(key=lambda x: x['published_dt'], reverse=True)

        total_sentiment_score = sum(item['Score'] for item in news_items)
        self.screen_region.markdown(f"<h5 style='color: gray;'>Total Sentiment Score: {total_sentiment_score:.2f}</h5>", unsafe_allow_html=True)

        df = pd.DataFrame(news_items)[['Published', 'Title', 'Sentiment', 'Score', 'Link']]

        def _color_sentiment(val):
            color = {'Positive': 'green', 'Negative': 'red', 'Neutral': 'gray'}.get(val, 'gray')
            return f'color: {color}'

        styled = df.style.map(_color_sentiment, subset=['Sentiment'])
        self.screen_region.dataframe(
            styled,
            column_config={'Link': st.column_config.LinkColumn('Link', display_text='Read more')},
            hide_index=True,
            use_container_width=True,
        )

