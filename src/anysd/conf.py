import cfg_load
import redis
import os

config_path = os.environ.get("ANYSD_CONFIG_FILE", 'config.yaml')
config = cfg_load.load(config_path)

nav: dict = config.get('navigation')
if nav:
    back_symbol = str(nav.get('back_symbol'))
else:
    back_symbol = '0'

if nav:
    home_symbol = str(nav.get('home_symbol'))
else:
    home_symbol = '00'

rc = config['redis']
r = redis.Redis(host=rc.get('host', 'localhost'), port=rc.get('port', 6379), charset="utf-8",
                decode_responses=True, db=rc.get('db', 4))


class FormBackError(IndexError):
    """raised when you try to go back from a form, but there's no more step to go back to"""
    pass


class NavigationBackError(IndexError):
    """raised when you go back in a navigation"""
    pass


class NavigationInvalidChoice(Exception):
    """raised when an invalid choice is selected in navigation"""
