# OPENCORE - ADD
import datetime

# Import required modules
from methods.regular.regular_api import *
from dataclasses import dataclass
from shared.database.task.job.job_launch import JobLaunch, JobLaunchQueue
from shared.utils.job_launch_utils import job_launch_limits_with_permission_check
from shared.database.task.job.job_working_dir import JobWorkingDir
from shared.regular.regular_log import log_has_error
import requests
from flask import jsonify, request, sessionMaker


@dataclass
class TaskTemplateLaunchInvoker:
    def __init__(self, task_template: Job, session: any):
        self.task_template = task_template
        self.session = session

    def enqueue_task_template_launch(self, member_created=None):
        # Create a JobLaunch entry for future queries.
        job_launch = JobLaunch.new(
            session=self.session,
            add_to_session=True,
            flush_session=True,
            job=self.task_template,
            member_created=member_created
        )

        # Now Create a new queue element for the worker to process it.
        JobLaunchQueue.add_to_queue(session=self.session,
                                    job_launch=job_launch,
                                    add_to_session=True,
                                    flush_session=True)

        regular_methods.commit_with_rollback(session=self.session)
        self.notify_workers()

    def notify_workers(self):
        regular_methods.transmit_interservice_request(
            message='new_job_launch_queue_item',
            logger=logger,
            service_target='walrus'
        )


def check_integrations_support(session, task_template, log):
    if not task_template.interface_connection:
        return log

    connection = task_template.interface_connection
    files_count = task_template.get_attached_files(session=session, return_kind='count')

    if connection.integration_name == 'scale_ai':
        if task_template.instance_type not in ['box', 'polygon']:
            log['error'][
                'labelbox_interface'] = f'Cannot use instance type {task_template.instance_type} for ScaleAI. ' \
                                         f'Please choose one of "box", "polygon" or "line".'
            if files_count == 0:
                log['error']['file_count'] = 'Datasets must contain at least 1 file to launch.'

    return log


def check_exam_has_assignees(session, task_template):
    """
    Check if the exam template has any assignees.

    :return:
    """
    if task_template.type != 'exam_template':
        return True

    assignee_list = task_template.get_assignees(session)
    return len(assignee_list) > 0


@routes.route('/api/v1/job/launch', methods=['POST'])
def task_template_launch_api():
    """
    API endpoint to launch a job template.

    """
    spec_list = [{"job_id": int}]

    log, input, untrusted_input = regular_input.master(request=request, spec_list=spec_list)
    if len(log["error"].keys()) >= 1:
        return jsonify(log=log), 400

    with sessionMaker.session_scope() as session:

        task_template = Job.get_by_id(session, input['job_id'])

        ### MAIN
        log = job_launch_limits_with_permission_check(
            session=session,
            job=task_template,
            job_id=task_template.id,  # Work around to satisfy permissions system
            log=log)

        # Check integrations support
        log = check_integrations_support(session, task_template, log)
        if log_has_error(log):
            return jsonify(log=log), 400

        check_exam_assignees_ok = check_exam_has_assignees(session, task_template)

        if not check_exam_assignees_ok:
            log['error']['check_assignees'] = 'Exam must have at least 1 assignee.'
            return jsonify(log=log), 400

        # Enqueue Job Launch for future processing.
        task_template_launcher = TaskTemplateLaunchInvoker(task_template=task_template, session=session)
        member = None
        user = User.get(session)
        if user:
            member = user.member

        task_template_launcher.enqueue_task_template_launch(member_created=member)

        user_email = None
        member_id = None
        if user:
            user_email = user.email
            member_id = user.member_id

        Event.new(
            kind="job_launch",
            session=session,
            member_id=member_id,
            success=True,
            email=user_email
        )

        if task_template.pro_network is True:
            pass
            # Not available yet for open core installs

        log['success'] = True

        out = jsonify(log=log)
        return out, 200
