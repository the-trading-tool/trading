from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.common.exceptions import NoSuchElementException       
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import WebDriverException
from tradinglib import tools
import re

class WebTools(tools.Tools):
    
    chromeOptions = webdriver.ChromeOptions()
    chromeOptions.page_load_strategy = 'none'
#    chromeOptions.add_argument("--headless")
    chromeOptions.add_argument("--enable-javascript")
    chromeOptions.add_argument("--web-security=false")
    chromeOptions.add_argument("--disable-extensions")
    #chromeOptions.add_argument(f"--user-data-dir={os.environ['TEMP']}")
    chromeOptions.add_argument('--ignore-certificate-errors')
    #chromeOptions.add_argument('--profile-directory=Profile1')
    chromeOptions.add_argument("--window-size=1280,1024")
    chromeOptions.add_argument("--start-maximized")
    chromeOptions.add_argument("--disable-gpu")  # GPU deaktivieren (optional)
    chromeOptions.add_argument("--no-sandbox")  # Für root-Umgebungen notwendig
    chromeOptions.add_argument("--disable-dev-shm-usage")  # Shared Memory-Probleme vermeiden
    chromeOptions.add_argument("--enable-unsafe-swiftshader")
    chromeOptions.add_argument('--ignore-certificate-errors-spki-list')
    chromeOptions.add_argument('--ignore-ssl-errors')    

    def_to = 5
    
    def __init__(self, headless=False):

        self.By = By
        self.Keys = Keys
        self.headless = headless

        if self.headless:
            #from pyvirtualdisplay import Display
            # define and start a virtual display
            #self.display = Display(visible=0, size=(1920, 1080))
            #self.display.start()
            pass
        #self.d = self.init_webdriver()

    def init_webdriver(self, def_to = 20):
        
        self.d = webdriver.Chrome(options=self.chromeOptions)
        self.d.implicitly_wait(def_to)
        self.d.set_page_load_timeout(def_to)

    def quit_webdriver(self):
        """
        quits a global driver object (d)
        used to process web pages with selenium
        (deletes chromedriver)
        """
        self.d.quit()

    def get(self, path):
        """
        Calls the given web page by the url given,
        provides a global driver object (d).
        Is used to process web pages with selenium
        """

        if 1:
        #try:
            self.d.get(path)
        #except Exception:
        #    print(f'potential issue connecting to website {path}.')
        #    pass

    # check if a html tag can be found by its xpath
    def check_exists_by_xpath(self, path, duration = 0):
        """
        synonyme for check_exists(path, By.XPATH)
        """
        return self.check_exists(path, By.XPATH, duration)

    # check if a html tag can be found by its class name
    def check_exists_by_class_name(self, path, duration = 0):
        """
        synonyme for check_exists(class, By.CLASS_NAME)
        """
        return self.check_exists(path, By.CLASS_NAME, duration)

    # check if a html tag can be found by its css selector
    def check_exists_by_css_selector(self, path, duration = 0):
        """
        synonyme for check_exists(selector, By.CSS_SELECTOR)
        """
        return self.check_exists(path, By.CSS_SELECTOR, duration)

    # check if a html element can be identified by it characteristics
    def check_exists(self, selector, bytype = By.XPATH, timeout = 0, obj = None):
        """
        used to check if a html element can be identified by its characteristics
        perform a test on a web page element and returns False,
        or, if found, the selenium.object of the element which equals to (True)
        """
        element = None
        if obj == None:
            obj = self.d
        
        self.d.implicitly_wait(timeout)

        if bytype == By.XPATH:
            try:
                element  = obj.find_element(By.XPATH, selector)
            except Exception:
                pass

        if bytype == By.CSS_SELECTOR:
            try:
                element  = obj.find_element(By.CSS_SELECTOR, selector)
            except Exception:
                pass

        if bytype == By.CLASS_NAME:
            try:            
                element  = obj.find_element(By.CLASS_NAME, selector)
            except Exception:
                pass
    
        self.d.implicitly_wait(self.def_to)

        if element == None:
            return False
        else:
            return element

    def find_element(self, selector, bytype = By.CSS_SELECTOR, obj = None ):
        """
        perform a test on a web page element and return empty string '',
        or the object of the element (True)
        """
        element = ''
        if obj == None:
            obj = self.d

        #if 1:
        if bytype == By.XPATH:
            try:
                element = obj.find_element(By.XPATH, selector)
            except Exception:
                pass

        if bytype == By.CSS_SELECTOR:
            try:
                element = obj.find_element(By.CSS_SELECTOR, selector)
            except Exception:
                pass

        if bytype == By.CLASS_NAME:
            try:        
                element = obj.find_element(By.CLASS_NAME, selector)
            except Exception:
                pass

        if bytype == By.TAG_NAME:
            try:
                element = obj.find_element(By.TAG_NAME, selector)
            except Exception:
                pass
        
        return element

    def find_elements(self, selector, bytype = By.CSS_SELECTOR, obj = None ):
        """
        perform a test (by given selector or xpath)
        on a web page element and return empty string '',
        or the object of the elements (True)
        """

        element = ''
        if obj == None:
            obj = self.d

        #if 1:
        if bytype == By.XPATH:
            try:
                element = obj.find_elements(By.XPATH,selector)
            except Exception:
                pass

        if bytype == By.CSS_SELECTOR:
            try:
                element = obj.find_elements(By.CSS_SELECTOR,selector)
            except Exception:
                pass

        if bytype == By.CLASS_NAME:
            try:        
                element = obj.find_elements(By.CLASS_NAME,selector)
            except Exception:
                pass

        if bytype == By.TAG_NAME:
            try:
                element = obj.find_elements(By.TAG_NAME,selector)
            except Exception:
                pass
        
        return element

    #get a clickable html element 
    def get_element(self, path, by, timeout = 20):
        """
        this is an additional option to get element objects and may be the safest.
        return the by path or selector given
        element by using EC to ensure module waits until the element is visible
        """
        element = None

        try:
            wait = WebDriverWait(self.d, timeout)
        except TimeoutException:
            print(f'get_element: {path} webdriver wait timeout.')
            pass

        if wait:
            try:
                element = wait.until(EC.element_to_be_clickable((by, path)))
            except TimeoutException:
                print(f'get_element: {path} timeout EXCEPTION.')
                pass
            except Exception:
                print(f'get_element: {path} other EXCEPTION.')
                pass        

        return element

    #get all elements  
    def get_elements(self, path, by, timeout = 20):
        """
        this is an additional option to get element objects and may be the safest.
        return the by path or selector given
        elements by using EC to ensure module waits until the element is visible
        """

        #print(by + ": " + path)
        elements = None
    
        try:
            elements = self.d.find_elements(by, path)
        except TimeoutException:
            print("get_elements: timeout EXCEPTION")
            pass
        except Exception:
            print("get_elements: other EXCEPTION")
            pass

        return elements

    def get_action_chains(self):
        return ActionChains(self.d)

    def perform_action_element(self, path, by = By.CSS_SELECTOR):
        """
        used to select and navigate to, for example,
        a drop down menu with hidden links
        just path the element containing the menu to it (hover)
        afterwards call click_item with the element pointing to the link
        """
        try:
            action = ActionChains(self.d)
            element = self.get_element(path, by )
            action.move_to_element(element).perform()
            return True
        except Exception:
            return False

    def get_item(self, path, by, timeout = 20):
        """
        synonyme for get_element
        """
        return self.get_element(path, by, timeout = 20)
   
    #press a button in a web page
    def click_item(self, path, by = By.XPATH, timeout = 20):
        """
        uses selenium webdriver to performa click on a item
        returns the item if found (True)
        or False it not found
        """
        print(f'click item/element/button: {path}')
        button = None
        try:
            button = self.get_element(path, by, timeout)
            button.click()
            return button
        except Exception:
            pass

        try:
            # even if this failed the first attempt, we do it again ;-)
            button = self.check_exists(path, by, timeout)
            button.click()
            return button
        except Exception:
            pass
            return False

    def text_from_element(self, element):
        """
        returns the text of a html element 
        or an empty string
        """

        try:
            if element == None:
                return ''
            else:
                return element.text
        except Exception:
            return ''
            
    def attribute_from_element(self, element, attribute = 'href'):
        """
        returns any attribute (by default href) of a html element 
        or an empty string
        """
        try:
            if element == None:
                return ''
            else:
                return element.get_attribute(attribute)
        except Exception:
            return ''
            
    #convert float into german decimal
    def german_decimals(self, text):
        """
        convert english (.) to german decimal points (,)
        e.g. input of a value format xxx,xxx.yy
        returns a value of format xxx.xxx,yy
        """
        # due to an issue on wsj we need this
        if text.find(" ") >= 0:
            return text.replace(" ",",")
        else:
            return text.replace(",",":::").replace(".",",").replace(":::","")

    def swap_decimals(self, text):
        """
        swaps german and english decimal points (,) to (.) and vice versa
        """
        return text.replace(",",":::").replace(".",",").replace(":::",".")

    def get_content_by_regex(self, regex, content, item = 1):
        """
        this procedure expects an regular expression and matches it on a string with given content
        it then returns the n-th item found in the content
        if no match it returns an empty sting
        """
        result = ''
        try:
                result = re.search(regex, content, re.MULTILINE).group(item)
                return result
        except Exception:
                return ''
