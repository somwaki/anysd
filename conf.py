import cfg_load

config_path = 'config.yaml'
config = cfg_load.load(config_path)


class FormBackError(IndexError):
    """raised when you try to go back from a form, but there's no more step to go back to"""
    pass
