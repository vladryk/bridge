# from jsb import LOG
from __init__ import LOG

class Bridge(object):
    def __init__(self, sfdc_client, jira_client, store, config):
        self.sfdc_client = sfdc_client
        self.jira_client = jira_client
        self.store = store

        self.issue_jql = config['jira_issue_jql']

        self.priority_map = config['jira_priority_map']
        self.fallback_priority = config['jira_fallback_priority']

        self.jira_reference_field = config['jira_reference_field']

        self.jira_identity = jira_client.current_user()

    def sync_issues(self):
        LOG.debug('Querying JIRA: %s', self.issue_jql)
        for issue in self.jira_client.search_issues(self.issue_jql,
                                                    fields='assignee,attachment,comment,*navigable'):
            try:
                self.sync_issue(issue)
            except KeyboardInterrupt:
                LOG.error('Operation canceled by user')
                return
            except:
                LOG.exception('Failed to sync issue: %s', issue.key)

        LOG.debug('Sync finished')
    
    def sync_issue(self, issue):
        LOG.debug('Syncing JIRA issue: %s', issue.key)
        ticket = self.ensure_ticket(issue)
        if not ticket:
            return

        self.sync_priority(issue, ticket)
        self.sync_jira_reference(issue, ticket)
        self.sync_comments_from_jira(issue, ticket)
        self.sync_comments_to_jira(issue, ticket)

        self.sync_status(issue, ticket)

    def ensure_ticket(self, issue):
        ticket_id = self.store.hget('issue_to_ticket_id', issue.key)
        if not ticket_id:
            ticket_id = getattr(issue.fields, self.jira_reference_field)

        # TODO Search Salesforce proxyTickets by external ID once its changed to string

        if not ticket_id:
            if not self.is_issue_eligible(issue):
                LOG.debug('Skipping previously untracked, ineligible issue')
                return

            ticket_id = self.create_ticket(issue)
        
        self.store.hset('issue_to_ticket_id', issue.key, ticket_id)

        return self.sfdc_client.ticket(ticket_id)

    def is_issue_eligible(self, issue):
        return True

    def create_ticket(self, issue):
        LOG.info('Creating ticket for issue')

        data = {
            'Subject__c': issue.fields.summary,
            'Description__c': issue.fields.description,
            # 'External_id__c': issue.key, # TODO External ID needs to be string
        }

        result = self.sfdc_client.create_ticket(data)

        return result['id']

    def sync_priority(self, issue, ticket):
        sfdc_priority = self.priority_map.get(issue.fields.priority.name, self.fallback_priority)

        if ticket['Priority__c'] == sfdc_priority:
            return

        LOG.info('Updating priority on SFDC: %s', sfdc_priority)

        data = {
            'Priority__c': sfdc_priority
        }

        self.sfdc_client.update_ticket(ticket['Id'], data)

    def sync_jira_reference(self, issue, ticket):
        if getattr(issue.fields, self.jira_reference_field) != ticket['Id']:
            LOG.info('Updating JIRA reference for ticket: %s', ticket['Id'])
            issue.update(fields={self.jira_reference_field: ticket['Id']})

    def sync_status(self, issue, ticket):
        pass

    def sync_comments_from_jira(self, issue, ticket):
        for comment in issue.fields.comment.comments:
            if comment.author.name == self.jira_identity:
                LOG.debug('Skipping my own JIRA comment: %s', comment.id)
                continue

            if self.store.sismember('seen_jira_comments', comment.id):
                LOG.debug('Skipping seen JIRA comment: %s', comment.id)
                continue

            LOG.info('Copying JIRA comment to SFDC: %s', comment.id)

            data = {
                'Comment__c': comment.body,
                'related_id__c': ticket['Id'],
            }

            self.sfdc_client.create_ticket_comment(data)

            self.store.sadd('seen_jira_comments', comment.id)

    def sync_comments_to_jira(self, issue, ticket):
        comments = self.sfdc_client.ticket_comments(ticket['Id'])
        for i in comments:
            print(i)
