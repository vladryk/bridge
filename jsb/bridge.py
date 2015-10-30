from dateutil.parser import parse
# from jsb import LOG
from __init__ import LOG  # FIXME

ACTION_HANDLER_FORMAT = 'handle_{}'


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
        self.jira_solved_statuses = config['jira_solved_statuses']

        self.jira_status_actions = self.parse_status_action_defs(config['jira_status_actions'])
        self.zd_status_actions = self.parse_status_action_defs(config['zd_status_actions'])

    def sync_issues(self):
        LOG.debug('Querying JIRA: %s', self.issue_jql)
        for issue in self.jira_client.search_issues(
                self.issue_jql, fields='assignee,attachment,comment,*navigable'):

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
        self.sync_subject_description(issue, ticket)

        self.sync_status(issue, ticket)

    def ensure_ticket(self, issue):  # FIXME
        ticket = None
        ticket_id = self.store.hget('issue_to_ticket_id', issue.key)
        if not ticket_id:
            ticket_id = getattr(issue.fields, self.jira_reference_field)

        if ticket_id:
            ticket = self.sfdc_client.ticket(ticket_id)
        # TODO Search Salesforce proxyTickets by external ID once its changed to string

        if not ticket:
            if not self.is_issue_eligible(issue):
                LOG.debug('Skipping previously untracked, ineligible issue')
                return False

            LOG.info('Creating SF ticket for JIRA issue')
            ticket_id = self.create_ticket(issue)
        elif ticket['Status__c'] == 'closed':
            if not self.is_issue_eligible(issue):
                LOG.debug('Skipping previously closed, ineligible issue')
                return False

            LOG.info('Creating followup SF ticket for JIRA issue')
            ticket = self.create_followup_ticket(issue, ticket)

        self.store.hset('issue_to_ticket_id', issue.key, ticket_id)

        return self.sfdc_client.ticket(ticket_id)

    def is_issue_eligible(self, issue):
        """
        Determines if an untracked or previously closed issue is eligible for creation in Zendesk

        :param issue: `jira.resources.Issue` object
        :return: Whether or not issue is eligible
        """
        if issue.fields.status.name in self.jira_solved_statuses:
            # Ignore issues that have already been marked solved
            return False

        # if issue.fields.assignee and issue.fields.assignee.name != self.jira_identity:  # FIXME
        #     # Ignore issues that have already been assigned to someone other than us
        #     return False

        return True

    def create_followup_ticket(self, issue, previous_ticket):
        pass

    def create_ticket(self, issue):
        LOG.info('Trying to create ticket for issue %s', issue.key)
        assignee_name = getattr(issue.fields.assignee, 'name', '')
        reporter = getattr(issue.fields.reporter, 'displayName', '')
        data = {
            'Subject__c': issue.fields.summary,
            'Description__c': issue.fields.description,
            'External_id__c': issue.key,  # TODO External ID needs to be string
            'Requester__c': reporter,
            'Assignee__c': assignee_name,
            'Status__c': 'new'
        }

        result = self.sfdc_client.create_ticket(data)
        LOG.info('Successful create ticket %s,  for issue %s', result['id'], issue.key)

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

    def sync_comments_from_jira(self, issue, ticket):
        for comment in issue.fields.comment.comments:
            if comment.author.name == self.jira_identity:
                LOG.debug('Skipping my own JIRA comment: %s', comment.id)
                continue

            if self.store.sismember('seen_comments_id', comment.id):
                LOG.debug('Skipping seen JIRA comment: %s', comment.id)
                continue
            else:
                comment_from_sf = self.sfdc_client.ticket_comment(comment.id)  # FIXME need get all comments
                if comment_from_sf['totalSize'] != 0:
                    LOG.debug('Skipping seen SF comment: %s', comment.id)
                    self.store.sadd('seen_comments_id', comment.id)
                    continue

            LOG.info('Copying JIRA comment to SFDC: %s', comment.id)

            data = {
                'Comment__c': comment.body,
                'related_id__c': ticket['Id'],
                'external_id__c': comment.id
            }

            self.sfdc_client.create_ticket_comment(data)
            self.store.sadd('seen_comments_id', comment.id)

    def sync_comments_to_jira(self, issue, ticket):
        comments = self.sfdc_client.ticket_comments(ticket['Id'])
        for comment in comments:
            if self.store.sismember('seen_comments_id', comment['external_id__c']):
                LOG.debug('Skipping seen SalesForce comment: %s', comment['Id'])
                continue

            LOG.info(
                'Copying SalesForce comment to JIRA issue: %s',
                comment['Id'])

            issue_comment = self.jira_client.add_comment(issue, comment['Comment__c'])

            data = {
                'external_id__c': issue_comment.id
            }

            LOG.info(
                'Update SalesForce comment with JIRA comment-id: %s',
                issue_comment.id)

            self.sfdc_client.update_comment(comment['Id'], data)
            self.store.sadd('seen_comments_id', issue_comment.id)

    def sync_subject_description(self, issue, ticket):
        if (issue.fields.description != ticket['Description__c'] or
                    issue.fields.summary != ticket['Subject__c']):

            parse_issue_time = parse(issue.fields.updated)
            utc_time_issue = parse_issue_time.utctimetuple()
            parse_ticket_time = parse(ticket['LastModifiedDate'])
            utc_time_ticket = parse_ticket_time.utctimetuple()

            if utc_time_issue >= utc_time_ticket:
                LOG.info(
                    'Update SalesForce subject, description. Ticket %s',
                    ticket['Id'])
                self.sfdc_client.update_ticket(
                    ticket['Id'],
                    {'Description__c': issue.fields.description,
                     'Subject__c': issue.fields.summary})
            else:
                LOG.info(
                    'Update Jira summary, description. Jira ticket %s',
                    issue.key)
                issue.update(fields={'description': ticket['Description__c'],
                                     'summary': ticket['Subject__c']})

    def sync_status(self, issue, ticket):
        """
        Transitions the status on both sides when out of sync, with preference
        for the JIRA status

        :param ctx: `SyncContext` object
        """
        last_seen_jira_status = self.store.get('last_seen_jira_status:{}'.format(issue.key))
        last_seen_zd_status = self.store.get('last_seen_zd_status:{}'.format(ticket['Id']))

        LOG.debug('JIRA status: %s; SF status: %s', issue.fields.status.name, ticket['Status__c'])

        #owned = issue.fields.assignee.name == self.jira_identity
        owned = True
        if not owned:
            LOG.debug('Issue not owned by us, action defs without "force" will not apply')

        jira_status_changed = last_seen_jira_status != issue.fields.status.name
        if jira_status_changed:
            LOG.debug('JIRA status changed')

        zd_status_changed = last_seen_zd_status != ticket['Status__c']
        if zd_status_changed:
            LOG.debug('Zendesk status changed')

        self.process_status_actions(issue, ticket, self.jira_status_actions, jira_status_changed, owned)
        self.process_status_actions(issue, ticket, self.zd_status_actions, zd_status_changed, owned)

        self.store.set('last_seen_jira_status:{}'.format(issue.key), issue.fields.status.name)
        self.store.set('last_seen_zd_status:{}'.format(ticket['Id']), ticket['Status__c'])

    def process_status_actions(self, issue, ticket, action_defs, changed, owned):
        """
        Runs matching status action definitions against an issue/ticket pair

        :param action_defs: list of `ActionDefinition` objects
        :param changed: True if status has changed
        :param owned: True if issue is owned by bot
        """
        while True:
            match = False

            for action_def in action_defs:
                if not owned and not action_def.force:
                    continue

                if not (issue.fields.status.name in action_def.jira_status and
                            ticket['Status__c'] in action_def.zd_status):
                    continue

                LOG.debug('Matched action def: %s', action_def.description)
                match = True

                for action in action_def.actions:
                    if action.only_once and not changed:
                        LOG.debug('Skipping action marked only_once: %s', action.description)
                        continue

                    try:
                        LOG.info('Performing action: %s', action.description)
                        action.handle(ctx)
                    except:
                        LOG.exception('Failed to perform action')
                        return

                break

            if not match:
                LOG.debug('No action defs matched')
                break

    def parse_status_action_defs(self, action_defs):
        """
        Parses a list of status action definitions

        :param action_defs: list of dicts
        :return: list of `ActionDefinition` objects
        """
        results = []

        for action_def in action_defs:
            actions = []

            for action in action_def['actions']:
                # pop is used so remaining dict entries can be passed directly to action
                handler_name = ACTION_HANDLER_FORMAT.format(action.pop('type'))
                description = action.pop('description')
                only_once = action.pop('only_once', False)

                actions.append(Action(
                    handler=getattr(self, handler_name),
                    description=description,
                    params=action,
                    only_once=only_once,
                ))

            results.append(ActionDefinition(
                jira_status=action_def['jira_status'],
                zd_status=action_def['zd_status'],
                actions=actions,
                description=action_def['description'],
                force=action_def.get('force', False),
            ))

        return results

    def handle_update_ticket(self, ctx, **kwargs):
        pass
        # """
        # Handler for the `update_ticket` action type
        #
        # :param ctx: `SyncContext` object
        # """
        # ctx.ticket = ctx.ticket.update(**kwargs)

    def handle_add_ticket_tags(self, ctx, tags, **kwargs):
        pass
        # """
        # Handler for the `add_ticket_tags` action type
        #
        # :param ctx: `SyncContext` object
        # :param tags: list of tag names to add
        # """
        # absent_tags = []
        # for tag in tags:
        #     if tag not in ctx.ticket.tags:
        #         absent_tags.append(tag)
        #
        # if absent_tags:
        #     ctx.ticket.add_tags(*absent_tags)
        #     self.refresh_ticket(ctx)

    def handle_remove_ticket_tags(self, ctx, tags, **kwargs):
        pass
        # """
        # Handler for the `remove_ticket_tags` action type
        #
        # :param ctx: `SyncContext` object
        # :param tags: list of tag names to remove
        # """
        # present_tags = []
        # for tag in tags:
        #     if tag in ctx.ticket.tags:
        #         present_tags.append(tag)
        #
        # if present_tags:
        #     ctx.ticket.remove_tags(*present_tags)
        #     self.refresh_ticket(ctx)

    def handle_transition_issue(self, ctx, name, **kwargs):
        pass
        # """
        # Handler for the `transition_issue` action type
        #
        # :param ctx: `SyncContext` object
        # :param name: name of the transition to perform
        # """
        # params = kwargs.copy()
        #
        # transitions = self.jira_client.transitions(ctx.issue)
        # transition_id = None
        # for transition in transitions:
        #     if transition['name'] == name:
        #         transition_id = transition['id']
        #         break
        #
        # if not transition_id:
        #     raise ValueError('Could not find transition: %s', name)
        #
        # self.jira_client.transition_issue(ctx.issue, transition_id, fields=params)
        # self.refresh_issue(ctx)

class ActionDefinition(object):
    def __init__(self, jira_status, zd_status, actions, description, force):
        self.jira_status = jira_status
        self.zd_status = zd_status
        self.actions = actions
        self.description = description
        self.force = force


class Action(object):
    def __init__(self, handler, params, description, only_once):
        self.handler = handler
        self.params = params
        self.description = description
        self.only_once = only_once

    def handle(self, ctx):
        if self.params:
            return self.handler(ctx, **self.params)
        else:
            return self.handler(ctx)
