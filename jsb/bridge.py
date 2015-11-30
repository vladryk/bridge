import jinja2
from dateutil.parser import parse
from jsb import LOG


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

        self.jira_possible_status = config['jira_possible_status']
        self.sf_possible_status = config['sf_possible_status']
        self.sf_ticket_close_status = config['sf_ticket_close_status']
        self.sf_ticket_solve_status = config['sf_ticket_solve_status']
        self.reference_jira_sf_statuses = config['reference_jira_sf_statuses']

        self.jira_resolution_status = config['jira_resolution_status']
        self.jira_description_field = config['jira_description_field']

        self.sf_initial_comment_format = jinja2.Template(config['sf_initial_comment_format'])
        self.sf_followup_comment_format = jinja2.Template(config['sf_followup_comment_format'])
        self.sf_comment_format = jinja2.Template(config['sf_comment_format'])
        self.jira_comment_format = jinja2.Template(config['jira_comment_format'])
        self.sf_signature_delimeter = config['sf_signature_delimeter']
        self.jira_url = config['jira_url']

        self.assignee_sf_name = config['assignee_sf_name']
        self.symantec_assignee_username = config['symantec_assignee_username']

    def sync_issues(self):
        LOG.debug('Querying JIRA: %s', self.issue_jql)
        for issue in self.jira_client.search_issues(self.issue_jql, fields='assignee,attachment,comment,*navigable'):

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
        self.sync_assignee(issue, ticket)
        self.sync_comments_from_jira(issue, ticket)
        self.sync_comments_to_jira(issue, ticket)
        self.sync_subject_description(issue, ticket)

        self.sync_status(issue, ticket)

    def ensure_ticket(self, issue):
        ticket = None
        ticket_id = self.store.hget('issue_to_ticket_id', issue.key)
        if not ticket_id:
            ticket_id = getattr(issue.fields, self.jira_reference_field)

        if ticket_id:
            ticket = self.sfdc_client.ticket(ticket_id)
            if not ticket:
                LOG.debug('Jira-issue has a link to SF-ticket, but '
                          'SF does not have a ticket with ID: %s', ticket_id)
        if not ticket:
            if not self.is_issue_eligible(issue):
                return False
            LOG.info('Creating SF ticket for JIRA issue %s', issue.key)
            ticket_id = self.create_ticket(issue)

        # elif ticket['Closed__c']:
        #     self.sfdc_client.update_ticket(ticket['Id'], data={'Closed__c': False})

        if ticket['Status__c'] == self.sf_ticket_close_status:
            if not self.is_issue_eligible(issue):
                return False
            LOG.info('Creating followup SF ticket for '
                     'JIRA issue %s', issue.key)
            ticket_id = self.create_followup_ticket(issue, ticket_id)

        self.store.hset('issue_to_ticket_id', issue.key, ticket_id)
        return self.sfdc_client.ticket(ticket_id)

    def is_issue_eligible(self, issue):
        """
        Determines if an untracked or previously closed issue is eligible for creation in SF

        :param issue: `jira.resources.Issue` object
        :return: Whether or not issue is eligible
        """
        if issue.fields.status.name in self.jira_solved_statuses:
            # Ignore issues that have already been marked solved
            LOG.debug('Skipping issue %s (issues that have already '
                      'been marked solved)', issue.key)
            return False

        if issue.fields.assignee and issue.fields.assignee.name != self.jira_identity:  # FIXME
            # Ignore issues that have already been assigned to someone other than us
            LOG.debug('Skipping issue %s (issues that have already '
                      'been assigned to someone other than us)', issue.key)
            return False

        return True

    def create_followup_ticket(self, issue, closed_ticket_id):
        LOG.debug('Trying to create new ticket for re-opened issue %s', issue.key)
        assignee_name = getattr(issue.fields.assignee, 'name', self.jira_identity)
        reporter = getattr(issue.fields.reporter, 'displayName', '')
        discription = getattr(issue.fields, 'description', '')
        comment = self.sf_followup_comment_format.render(issue=issue,
                                                         jira_url=self.jira_url)
        data = {
            'Subject__c': issue.fields.summary,
            'Description__c': discription,
            'External_id__c': issue.key,
            'Requester__c': reporter,
            'Assignee__c': assignee_name,
            'Closed_Case_Id__c': closed_ticket_id
        }

        result = self.sfdc_client.create_ticket(data)
        LOG.debug('Successful create new ticket %s,  for old issue %s', result['id'], issue.key)

        data = {
                'Comment__c': comment,
                'related_id__c': result['id'],
               }

        self.sfdc_client.create_ticket_comment(data)

        LOG.debug('Start bind old jira comments for new ticket')
        self._change_sf_comments_id(issue, result['id'])
        LOG.debug('Finish bind old jira comments for new ticket')

        return result['id']

    def _change_sf_comments_id(self, issue, new_ticket_id):
        for comment in issue.fields.comment.comments:
            comment_from_sf = self.sfdc_client.ticket_comment(comment.id)
            if comment_from_sf['totalSize'] != 0:
                data = {
                    'related_id__c': new_ticket_id,
                }

                self.sfdc_client.update_comment(comment_from_sf['records'][0]['Id'], data)
                self.store.sadd('seen_comments_id', comment.id)

    def create_ticket(self, issue):
        LOG.info('Trying to create ticket for issue %s', issue.key)
        assignee_name = getattr(issue.fields.assignee, 'name', self.jira_identity)
        reporter = getattr(issue.fields.reporter, 'displayName', '')

        comment = self.sf_initial_comment_format.render(issue=issue,
                                                        jira_url=self.jira_url)

        data = {
            'Subject__c': issue.fields.summary,
            'Description__c': comment,
            'External_id__c': issue.key,
            'Requester__c': reporter,
            'Assignee__c': assignee_name,
        }

        result = self.sfdc_client.create_ticket(data)
        LOG.info('Successful create ticket %s,  for issue %s', result['id'], issue.key)
        issue.update(fields={self.jira_description_field: comment})

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
                comment_from_sf = self.sfdc_client.ticket_comment(comment.id)
                if comment_from_sf['totalSize'] != 0:
                    LOG.debug('Skipping seen SF comment: %s', comment.id)
                    self.store.sadd('seen_comments_id', comment.id)
                    continue

            LOG.info('Copying JIRA (Jira issue %s) comment to SFDC: %s', issue.key, comment.id)

            comment_body = self.sf_comment_format.render(comment=comment)

            data = {
                'Comment__c': comment_body,
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
            if comment['external_id__c']:
                LOG.debug('Skipping seen SalesForce comment: %s '
                          '(comment has JIRA comment-id %s )',
                          comment['Id'], comment['external_id__c'])
                continue

            LOG.info(
                'Copying SalesForce comment %s ,  to JIRA issue %s',
                comment['Id'], issue.key)

            comment_body = self.jira_comment_format.render(comment=comment['Comment__c'],
                                                           created_at=comment['CreatedDate'],
                                                           created_by=comment['CreatedBy']['Name'])

            issue_comment = self.jira_client.add_comment(issue, comment_body)
            data = {'external_id__c': issue_comment.id}
            LOG.info(
                'Update SalesForce comment %s, with JIRA comment-id: %s',
                comment['Id'], issue_comment.id)

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

    def map_status_jira_sf(self, issue_status_name):
        return self.jira_possible_status.get(issue_status_name, 'None')

    def sync_status(self, issue, ticket):
        last_seen_jira_status = self.store.get('last_seen_jira_status:{}'.format(issue.key))
        last_seen_sf_status = self.store.get('last_seen_sf_status:{}'.format(ticket['Id']))

        LOG.debug('JIRA status: %s; SF status: %s', issue.fields.status.name, ticket['Status__c'])

        jira_status_changed = last_seen_jira_status != issue.fields.status.name
        if jira_status_changed:
            LOG.debug('JIRA status changed')

        sf_status_changed = last_seen_sf_status != ticket['Status__c']
        if sf_status_changed:
            LOG.debug('SF status changed')

        if jira_status_changed or sf_status_changed:
            new_issue_status, new_ticket_status = self.new_process_sync_status(
                issue, ticket, jira_status_changed, sf_status_changed)
            self.store.set('last_seen_jira_status:{}'.format(issue.key), new_issue_status)
            self.store.set('last_seen_sf_status:{}'.format(ticket['Id']), new_ticket_status)

    def process_sync_status(self, issue, ticket, jira_status_changed, sf_status_changed):
        status_name_issue = issue.fields.status.name
        forward_to = self.sf_possible_status.get(ticket['Status__c'])
        if not forward_to:
            LOG.info('Invalid value issue-status: %s for Ticket-status: %s',
                     issue.fields.status.name, ticket['Status__c'])
            sf_status_changed = True
            jira_status_changed = True

        if sf_status_changed and not jira_status_changed:
            transitions = self.jira_client.transitions(self.jira_client.issue(issue.key))
            available_transitions = dict((t['name'], t['id']) for t in transitions)

            # If current state for Issue -- Support Investigating (and OnHold - for Ticket) ---
            # we get error when we change Ticket-status to Solve. I think problem in
            # Issue-status workflow
            if issue.fields.status.name == forward_to[0] and len(forward_to) > 1:
                forward_to = forward_to[1:]

            LOG.info('Move-list for move Issue %s', forward_to)
            LOG.info('Start Issue-status %s', issue.fields.status.name)

            for status in forward_to:
                result = self.jira_client.transition_issue(issue, available_transitions[status])
                LOG.info('Moved status issue to %s', status)
                transitions = self.jira_client.transitions(self.jira_client.issue(issue.key))
                available_transitions = dict((t['name'], t['id']) for t in transitions)

            if ticket['Status__c'] in self.sf_ticket_solve_status:
                issue.update(fields={'resolution': self.jira_resolution_status})
            return forward_to[-1], ticket['Status__c']

        else:
            data = {
                'Status__c': self.map_status_jira_sf(status_name_issue)
            }
            self.sfdc_client.update_ticket(ticket['Id'], data)
            LOG.debug('Updated ticket status: %s', ticket['Id'])
            return status_name_issue, self.map_status_jira_sf(status_name_issue)

    def new_process_sync_status(self, issue, ticket, jira_status_changed, sf_status_changed):
        status_name_issue = issue.fields.status.name
        if sf_status_changed and not jira_status_changed:
            jira_status_from_conf = self.reference_jira_sf_statuses.get(status_name_issue)
            possible_ticket_status = jira_status_from_conf.get(ticket['Status__c'])
            if not jira_status_from_conf or not possible_ticket_status:
                LOG.info('You try to change SF status. '
                         'Jira status name now %s, '
                         'Jira status name from conf %s, '
                         'SF status %s, '
                         'Possible ticket status %s. '
                         'Please see conf file. SF status will not change',
                         status_name_issue, jira_status_from_conf,
                         ticket['Status__c'], possible_ticket_status)
                sf_status_changed = True
                jira_status_changed = True
        # forward_to = self.sf_possible_status.get(ticket['Status__c'])
        # if not forward_to:
        #     LOG.info('Invalid value issue-status: %s for Ticket-status: %s',
        #              issue.fields.status.name, ticket['Status__c'])
        #     sf_status_changed = True
        #     jira_status_changed = True

        if sf_status_changed and not jira_status_changed:
            workflow = self.reference_jira_sf_statuses.get(status_name_issue).get(ticket['Status__c'])
            if not workflow:
                LOG.info('Not found schema. Current Jira status: %s,'
                         ' SF status: %s', status_name_issue, ticket['Status__c'])
            transitions = self.jira_client.transitions(self.jira_client.issue(issue.key))
            available_transitions = dict((t['name'], t['id']) for t in transitions)
            LOG.info('For current Jira-state %s, possible statuses is: %s, List of moving statuses %s ',
                     status_name_issue, available_transitions, workflow)

            # If current state for Issue -- Support Investigating (and OnHold - for Ticket) ---
            # we get error when we change Ticket-status to Solve. I think problem in
            # Issue-status workflow
            # if issue.fields.status.name == forward_to[0] and len(forward_to) > 1:
            #     forward_to = forward_to[1:]

            # LOG.info('Move-list for move Issue %s', forward_to)
            # LOG.info('Start Issue-status %s', issue.fields.status.name)

            for status in workflow:
                LOG.info('Try to move issue to %s status', status)
                result = self.jira_client.transition_issue(issue, available_transitions[status])
                LOG.info('Moved issue to %s status', status)
                transitions = self.jira_client.transitions(self.jira_client.issue(issue.key))
                available_transitions = dict((t['name'], t['id']) for t in transitions)
                LOG.info('Now, possible statuses: %s ', available_transitions)

            # if ticket['Status__c'] in self.sf_ticket_solve_status:
            #     issue.update(fields={'resolution': self.jira_resolution_status})
            issue = self.refresh_issue(issue)
            return issue.fields.status.name, ticket['Status__c']

        else:
            data = {
                'Status__c': self.map_status_jira_sf(status_name_issue)
            }
            self.sfdc_client.update_ticket(ticket['Id'], data)
            LOG.debug('Updated ticket status: %s', ticket['Id'])
            return status_name_issue, self.map_status_jira_sf(status_name_issue)

    def sync_assignee(self, issue, ticket):
        last_seen_jira_assignee = self.store.get('last_seen_jira_assignee:{}'.format(issue.key))
        last_seen_sf_assignee = self.store.get('last_seen_sf_assignee:{}'.format(ticket['Id']))

        if issue.fields.assignee:
            LOG.debug('JIRA issue assigned: %s', issue.fields.assignee.name)

        elif not issue.fields.assignee:
            LOG.info('Assigning previously unassigned JIRA issue to bot')
            self.jira_client.assign_issue(issue, self.jira_identity)
            issue = self.refresh_issue(issue)

        ticket_assignee_name = ticket['Assignee__c']
        jira_assignee_name = getattr(issue.fields.assignee, 'name', None)

        if issue.fields.assignee.name != last_seen_jira_assignee:
            if jira_assignee_name != self.jira_identity:
                ticket_assignee_name = self.assignee_sf_name[0]
            else:
                ticket_assignee_name = self.assignee_sf_name[1]
            data = {'Assignee__c': ticket_assignee_name}
            self.sfdc_client.update_ticket(ticket['Id'], data)

        elif ticket['Assignee__c'] != last_seen_sf_assignee:
            if ticket['Assignee__c'] == self.assignee_sf_name[1]:
                jira_assignee_name = self.jira_identity
            elif ticket['Assignee__c'] == self.assignee_sf_name[0]:
                jira_assignee_name = self.symantec_assignee_username
            self.jira_client.assign_issue(issue, jira_assignee_name)

        self.store.set('last_seen_jira_assignee:{}'.format(issue.key), jira_assignee_name)
        self.store.set('last_seen_sf_assignee:{}'.format(ticket['Id']), ticket_assignee_name)

    def refresh_issue(self, issue):
        """
        Refresh issue from the JIRA API
        """
        return self.jira_client.issue(issue.key, fields='assignee,attachment,comment,*navigable')
