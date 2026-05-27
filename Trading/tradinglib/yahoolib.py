import requests
from bs4 import BeautifulSoup
import json

class Ticker():

    modul_list = ['financialData','quoteType','defaultKeyStatistics','assetProfile','summaryDetail'] # ,'earnings','indexTrend','industryTrend']
#    user_agent_headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}
    user_agent_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59'}
    yahoo_session = requests.Session()
    info = {}
    symbol = None
    crumb = None
    session_data = None
    corporate_events = None
    
    def get_crumb(self):
        response = self.yahoo_session.get('https://guce.yahoo.com/consent', headers=self.user_agent_headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        try:
            csrfTokenInput = soup.find('input', attrs={'name': 'csrfToken'})
            csrfToken = csrfTokenInput['value']
            sessionIdInput = soup.find('input', attrs={'name': 'sessionId'})
            sessionId = sessionIdInput['value']
            originalDoneUrl = 'https://finance.yahoo.com/'
            namespace = 'yahoo'
            self.session_data = {
                'agree': ['agree', 'agree'],
                'consentUUID': 'default',
                'sessionId': sessionId,
                'csrfToken': csrfToken,
                'originalDoneUrl': originalDoneUrl,
                'namespace': namespace,
            }
            self.yahoo_session.post(f'https://consent.yahoo.com/v2/collectConsent?sessionId={sessionId}', data=self.session_data, headers=self.user_agent_headers)
            self.yahoo_session.get(f'https://guce.yahoo.com/copyConsent?sessionId={sessionId}', headers=self.user_agent_headers)
        except Exception:
            pass

        self.crumb = self.yahoo_session.get('https://query2.finance.yahoo.com/v1/test/getcrumb', headers=self.user_agent_headers).text


    def get_data(self, module='defaultKeyStatistic'):

        query2 = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"

        for module in self.modul_list:

            url = f"{query2}{self.symbol}?modules={module}&ssl=True"
            response = self.yahoo_session.get(f"{url}&crumb={self.crumb}", headers=self.user_agent_headers)

            n_rd = {}
            try:
                rd = json.loads(response.content)['quoteSummary']['result'][0][module]
                for k, v in rd.items():
                    if isinstance(v, dict):
                        if v:
                            n_rd[k] = v['raw']
                        else:
                            n_rd[k] = ""
                    else:
                        n_rd[k] = v
                rd = None
            except Exception:
                pass
                
            self.info.update(n_rd)
               

    def get(self, name):
        value = ''
        try:
            value = self.info[name]
        except Exception:
            pass

        if value:
            if type(value) == int or type(value) == float:
                value = round(value,2)

            
        return value


    def __init__(self, name):
        if name:
            self.info = {}
            self.symbol = name
            if not self.crumb:
                self.get_crumb()
            self.get_data()
        

### options for instrument https://query2.finance.yahoo.com/v7/finance/options/NVDA
### timeseries https://query2.finance.yahoo.com/v8/finance/chart/NVDA
### does symbol exist https://query2.finance.yahoo.com/v6/finance/quote/validate?symbols=NVDA
### instrument insights https://query2.finance.yahoo.com/ws/insights/v2/finance/insights?symbol=NVDA

