import cfg_load
import redis
import os

config_path = os.environ.get("ANYSD_CONFIG_FILE", 'config.yaml')
configs = cfg_load.load(config_path)

environment = os.getenv('ENVIRONMENT', 'development')
config = configs.get(environment)

nav: dict = config.get('navigation')
if nav and 'back_symbol' in nav:
    back_symbol = str(nav.get('back_symbol'))
else:
    back_symbol = '0'

if nav and 'home_symbol' in nav:
    home_symbol = str(nav.get('home_symbol'))
else:
    home_symbol = '00'

rc = config.get('redis')
if 'connection' in rc:
    rc = rc.get('connection')

r = redis.Redis(host=rc.get('host', 'localhost'), port=rc.get('port', 6379), charset="utf-8",
                password=rc.get('password', ''), decode_responses=True, db=rc.get('db', 4))


class FormBackError(IndexError):
    """raised when you try to go back from a form, but there's no more step to go back to"""
    pass


class NavigationBackError(IndexError):
    """raised when you go back in a navigation"""
    pass


class NavigationInvalidChoice(Exception):
    """raised when an invalid choice is selected in navigation"""


class ImproperlyConfigured(Exception):
    """raised for all other anysd errors"""


class ParseError(ImproperlyConfigured):
    """parse error when parsing dictionary to create ussd navigation"""


class ConditionEvaluationError(Exception):
    """raised when an error occurs when calling condition evaluation function"""


class ConditionResultError(Exception):
    """raised when the condition evaluation function result is not in mapping keys"""


class TranslationError(Exception):
    """raised when translation for selected language cannot be found"""
