from time import sleep
from jobs.utils import create_job
from jobs.models import Job
from reports.models import *
import hashlib
from marks.models import MarkUnsafeCompare, MarkUnsafeConvert
from django.core.exceptions import ObjectDoesNotExist
from datetime import datetime
from users.models import Extended
from Omega.vars import JOB_CLASSES
from marks.ConvertTrace import ConvertTrace
from marks.CompareTrace import CompareTrace
from types import FunctionType


def populate_jobs(user):
    old_jobs = Job.objects.all()
    while len(old_jobs) > 0:
        for job in old_jobs:
            if len(job.children_set.all()) == 0:
                job.delete()
        old_jobs = Job.objects.all()

    kwargs = {
        'author': user,
        'type': '0',
        'description': "A lot of text (description)!",
        'global_role': '1',
    }

    for i in range(len(JOB_CLASSES)):
        kwargs['name'] = JOB_CLASSES[i][1]
        kwargs['pk'] = i + 1
        kwargs['type'] = JOB_CLASSES[i][0]
        create_job(kwargs)
        sleep(0.1)


class Population(object):

    def __init__(self, user, username=None):
        self.user = user
        self.jobs_updated = False
        self.functions_updated = False
        self.manager_password = None
        self.manager_username = username
        self.__population()
        self.something_changed = (self.functions_updated or
                                  self.manager_password is not None
                                  or self.jobs_updated)

    def __population(self):
        try:
            self.user.extended
        except ObjectDoesNotExist:
            self.__extend_user(self.user)
        manager = self.__get_manager()
        self.__populate_functions()
        if len(Job.objects.all()) == 0 and isinstance(manager, User):
            self.jobs_updated = True
            populate_jobs(manager)

    def __populate_functions(self):
        for func_name in [x for x, y in ConvertTrace.__dict__.items()
                          if type(y) == FunctionType and not x.startswith('_')]:
            description = getattr(ConvertTrace, func_name).__doc__
            func, crtd = MarkUnsafeConvert.objects.get_or_create(name=func_name)
            if crtd or description != func.description:
                self.functions_updated = True
            elif isinstance(description, str):
                func.description = description
                func.save()

        for func_name in [x for x, y in CompareTrace.__dict__.items()
                          if type(y) == FunctionType and not x.startswith('_')]:
            description = getattr(CompareTrace, func_name).__doc__
            func, crtd = MarkUnsafeCompare.objects.get_or_create(name=func_name)
            if crtd or description != func.description:
                self.functions_updated = True
            elif isinstance(description, str):
                func.description = description
                func.save()

    def __extend_user(self, user, role='1'):
        self.user = self.user
        extended = Extended()
        extended.first_name = 'Firstname'
        extended.last_name = 'Lastname'
        extended.role = role
        extended.user = user
        extended.save()

    def __get_manager(self):
        if self.manager_username is None:
            return None
        try:
            return User.objects.get(username=self.manager_username,
                                    extended__role='2')
        except ObjectDoesNotExist:
            pass
        manager = User()
        manager.username = self.manager_username
        manager.save()
        time_encoded = datetime.now().strftime("%Y%m%d%H%M%S%f%z")\
            .encode('utf8')
        password = hashlib.md5(time_encoded).hexdigest()[:8]
        manager.set_password(password)
        manager.save()
        self.__extend_user(manager, '2')
        self.manager_password = password
        return manager
