from argparse import ArgumentParser
import logging
import os
import sys

from jira import JIRA
import yaml

#from jsb import LOG
from __init__ import LOG  # FIXME
from salesforce import OAuth2, Client
from bridge import Bridge
from storage import FileBackend, Store


def configure_logger(level):
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

    LOG.addHandler(handler)
    LOG.setLevel(level)


def main():
    parser = ArgumentParser()
    parser.add_argument('-c', '--config-file', default='config.yml')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-Q', '--query')

    args = parser.parse_args()

    if args.debug:
        configure_logger(logging.DEBUG)
    else:
        configure_logger(logging.INFO)

    with open(args.config_file) as fp:
        config = yaml.load(fp)

    jira_client = JIRA(server=config['jira_url'],
                       basic_auth=(config['jira_username'], config['jira_password']))

    sfdc_oauth2 = OAuth2(client_id=config['sfdc_client_id'],
                         client_secret=config['sfdc_client_secret'],
                         username=config['sfdc_username'],
                         password=config['sfdc_password'],
                         auth_url=config['sfdc_auth_url'])

    sfdc_client = Client(sfdc_oauth2)

    storage_path = os.path.join(config['storage_dir'], 'state.yml')
    store = Store(FileBackend(storage_path))

    bridge = Bridge(sfdc_client, jira_client, store, config)

    if args.query:
        bridge.issue_jql = args.query

    bridge.sync_issues()

if __name__ == '__main__':
    main()
