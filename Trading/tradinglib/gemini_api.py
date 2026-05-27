from google import genai
import sys
import json
import yaml

#try:
#    sys.path.insert(0, "../tradinglib/")
#except ImportError:
#    print('No Import')

from tradinglib import ksplib

class GeminiApi():
    
    api_key = ""
    
    def __init__(self, question = ''):
        ksp = ksplib.Ksp()
        (api_key, _,_ ) = ksp.get_ksp('gapi').values()
        self.client = genai.Client(api_key=api_key)
        if not question == '':
            self.run_question(question)
        pass
    
    def run_question(self, question):
    
        response = self.client.models.generate_content(
            model='gemini-2.0-flash',
            contents=question
        )
        
        self.answer = response.text
    
if __name__ == "__main__":
    
    answer = GeminiApi(question="O'Reilly Automotive, Inc.").answer
    print(answer)

#print(json_string)
#message_dict = json.loads(json.dumps(json_string))
#print(type(message_dict))
#json_string = json_string.replace("'", "\"")
##json_object = eval(json.dumps(json_text))
#json_object = json.loads(json_string)
#print(json_object)
#print(json_object['candidates']['content'])
    pass