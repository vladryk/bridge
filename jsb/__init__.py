import logging
import sys

import yaml

def force_yaml_unicode():
    def unicode_str_constructor(loader, node):
        return unicode(loader.construct_scalar(node))

    yaml.add_constructor(u'tag:yaml.org,2002:str', unicode_str_constructor)

if sys.version_info < (3, 0):
    force_yaml_unicode()

LOG = logging.getLogger('jsb')
