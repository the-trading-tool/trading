import streamlit as st
import requests
import re
import feedparser
import nltk
import pandas as pd
import plotly.express as px
from datetime import datetime

# vader_lexicon herunterladen -- SSL-Bypass fuer eingeschraenkte Umgebungen
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

class YahooNewsSentiment:

    def __init__(self, ticker, screen_region = st):
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
 #       self.driver = self.init_driver()

 #   def init_driver(self):
 #       options = Options()
 #       options.add_argument("--headless")
 #       options.add_argument("--disable-gpu")
 #       options.add_argument("--no-sandbox")
 #       options.add_argument("--disable-dev-shm-usage")
 #       service = Service("chromedriver")
 #       driver = webdriver.Chrome(service=service, options=options)
 #       return driver

    def fetch_news(self):
        feed = feedparser.parse(self.url)
        articles = [{'title': entry.title, 'link': entry.link, 'published': entry.published} for entry in feed.entries]
        return articles
#        return feed

  #  def fetch_article_text(self, url):
  #      try:
  #          self.driver.get(url)
  #          time.sleep(3)  # Warten auf das Laden der Seite
  #          paragraphs = self.driver.find_elements(By.TAG_NAME, "p")
  #          text = ' '.join([para.text for para in paragraphs])
  #          return text if text else None
  #      except Exception:
  #          return None

    def fetch_article_text(self, url):
        try:
            response = requests.get(url, timeout=5)
            text = ' '.join(re.findall(r'<p>(.*?)</p>', response.text))
            return text if text else None
        except Exception:
            return None

    def analyze_sentiment(self, articles, single=False):
        if self.sia is None:
            # Sentiment-Analyse nicht verfuegbar (vader_lexicon fehlt)
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
        # Streamlit App
        self.screen_region.title("Yahoo Finance News Sentiment Analysis")

        articles = self.fetch_news()
        self.list_articles(articles)
        analyzed_articles = self.analyze_sentiment(articles)
        df = pd.DataFrame(analyzed_articles)
        
        #self.screen_region.write(f"### Sentiment Analyse für {self.ticker}")
        #self.screen_region.dataframe(df[['title', 'compound', 'pos', 'neu', 'neg']])
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

        news_items = []
        if articles:
            for article in articles:
                
                sentiment = self.analyze_sentiment(article['title'],True)
                compound_score = sentiment['compound']

                # Determine sentiment category
                if compound_score >= 0.05:
                    sentiment_category = "Positive"
                    color = "green"
                elif compound_score <= -0.05:
                    sentiment_category = "Negative"
                    color = "red"
                else:
                    sentiment_category = "Neutral"
                    color = "gray"

                news_items.append({
                    'title': article['title'],
                    'link': article['link'],
                    'published': article['published'],
                    'sentiment_category': sentiment_category,
                    'compound_score': compound_score,
                    'color': color
                })

                # Sort news items by published date (latest first)
                for item in news_items:
                    try:
                        item['published_dt'] = datetime.strptime(item['published'], "%a, %d %b %Y %H:%M:%S %z")
                    except ValueError:
                        item['published_dt'] = datetime.min  # Falls das Datum fehlt, nach hinten sortieren

            # Sort news items by published date (latest first) and take the latest 10
            latest_news_entries = sorted(news_items, key=lambda x: x['published_dt'], reverse=True) #[:10]

            # Sort the latest 10 news by sentiment score (highest to lowest)
#                            latest_news_entries.sort(key=lambda x: x['compound_score'], reverse=True)

            # Calculate total sentiment score for the latest 10 news items
            total_sentiment_score = sum(item['compound_score'] for item in latest_news_entries)

            # Display total sentiment score in red
            self.screen_region.markdown(f"<h3 style='color: gray;'>Total Sentiment Score: {total_sentiment_score:.2f}</h3>", unsafe_allow_html=True)

            # Display sorted news items
            for item in latest_news_entries:
                self.screen_region.markdown(f"**{item['title']}**")
                self.screen_region.markdown(f"[Read more]({item['link']})")
                self.screen_region.markdown(f"*Published: {item['published']}*")
                self.screen_region.markdown(f"Sentiment: <span style='color:{item['color']}'>{item['sentiment_category']}</span> (Score: {item['compound_score']:.2f})", unsafe_allow_html=True)
                self.screen_region.markdown("---")

