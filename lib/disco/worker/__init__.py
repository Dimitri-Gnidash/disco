#!/usr/bin/env python
"""
:mod:`disco.worker` -- Python Worker Interface
==============================================

In Disco, :term:`workers <worker>` do the brunt of the data processing work.
When a :class:`disco.job.Job` is created, it gets passed a :class:`Worker` instance,
which is responsible for defining the fields used by the :class:`disco.job.JobPack`.
In most cases, you don't need to define your own Worker subclass in order to run a job.
The Worker classes defined in :mod:`disco` will take care of the details
of creating the fields necessary for the :class:`disco.job.JobPack`,
and when executed on the nodes,
will handle the implementation of the :ref:`worker_protocol`.

.. hint:: Workers should not write anything to stderr.
          The worker uses stderr to communicate with the master.
          You can raise a :class:`disco.error.DataError`,
          to abort the worker and try again on another host.
          It is usually best to let the task fail if any exceptions occur:
          do not catch any exceptions from which you can't recover.
          When exceptions occur, the disco worker will catch them and
          signal an appropriate event to the master.


XXX

    :type  input: list of inputs or list of list of inputs
    :param input: Each input must be specified in one of the following ways:

                   * ``http://www.example.com/data`` - any HTTP address
                   * ``disco://cnode03/bigtxt/file_name`` - Disco address. Refers to ``cnode03:/var/disco/bigtxt/file_name``. Currently this is an alias for ``http://cnode03:[DISCO_PORT]/bigtxt/file_name``.
                   * ``dir://cnode03/jobname/`` - Result directory. This format is used by Disco internally.
                   * ``/home/bob/bigfile.txt`` - a local file. Note that the file must either exist on all the nodes or you must make sure that the job is run only on the nodes where the file exists. Due to these restrictions, this form has only limited use.
                   * ``raw://some_string`` - pseudo-address; instead of fetching data from a remote source, use ``some_string`` in the address as data. Useful for specifying dummy inputs for generator maps.
                   * ``tag://tagname`` - a tag stored in :ref:`DDFS` (*Added in version 0.3*)

                  (*Added in version 0.3.2*)
                  Tags can be token protected.
                  For the data in tags to be used as job inputs,
                  the tags should be resolved into the constituent urls or replica sets,
                  and provided as the value of the input parameter.

                  (*Added in version 0.2.2*):
                  An input entry can be a list of inputs:
                  This lets you specify redundant versions of an input file.
                  If a list of redundant inputs is specified,
                  the scheduler chooses the input that is located on the node
                  with the lowest load at the time of scheduling.
                  Redundant inputs are tried one by one until the task succeeds.
                  Redundant inputs require that the *map* function is specified.
"""
import cPickle, os, sys, traceback

class Worker(dict):
    """
    A :class:`Worker` is a :class:`dict` subclass,
    with special methods defined for serializing itself into a :class:`disco.job.JobPack`.

    Workers use the items stored in themselves to control how they process data,
    or how they create a JobPack.

    There are two responsibilities the Worker has:
        #. on the client side, create the jobpack
            including defining the executable that will be run on the nodes
        #. in Python, the Worker itself is by default used as the executable,
            in which case, it must also define what to do when it is run
            responsibilities of the worker on this case include requesting the Task
            and reporting the output

    the job is sent along with the worker, and items in the worker dict
        #. jobargs
        #. job
        #. worker

    :type  required_files: list of paths or dict
    :param required_files: additional files that are required by the worker.
                           Either a list of paths to files to include,
                           or a dictionary which contains items of the form
                           ``(filename, filecontents)``.
                           (*Added in version 0.2.3*)

    :type  required_modules: list of modules or module names
    :param required_modules: required modules to send to the worker.
                             Can also be a list of module objects.
                             (*Changed in version 0.4*):

    :type  scheduler: dict
    :param scheduler: options for the job scheduler.
                      The following keys are supported:

                       * *max_cores* - use this many cores at most
                                       (applies to both map and reduce).

                                       Default is ``2**31``.

                       * *force_local* - always run task on the node where
                                         input data is located;
                                         never use HTTP to access data remotely.

                       * *force_remote* - never run task on the node where input
                                          data is located;
                                          always use HTTP to access data remotely.

                      (*Added in version 0.2.4*)
    """
    def __init__(self, **kwargs):
        super(Worker, self).__init__(self.defaults())
        self.update(kwargs)

    @property
    def bin(self):
        """
        XXX
        """
        return os.path.join('lib', '%s.py' % self.__module__.replace('.', '/'))

    def defaults(self):
        """
        XXX
        """
        return {'map': None,
                'merge_partitions': False, # XXX: maybe deprecated
                'reduce': None,
                'required_files': {},
                'required_modules': None,
                'save': False,
                'scheduler': {},
                'partitions': 1,  # move to classic once partitions are dynamic
                'profile': False}

    def getitem(self, key, job, **jobargs):
        if key in jobargs:
            return jobargs[key]
        elif hasattr(job, key):
            return getattr(job, key)
        return self.get(key)

    def jobdict(self, job, **jobargs):
        """
        XXX
        """
        from disco.util import inputlist, ispartitioned, read_index
        def get(key):
            return self.getitem(key, job, **jobargs)
        has_map = bool(get('map'))
        has_reduce = bool(get('reduce'))
        input = inputlist(get('input') or (),
                          partition=None if has_map else False,
                          settings=job.settings)

        # -- nr_reduces --
        # ignored if there is not actually a reduce specified
        # XXX: master should always handle this
        if has_map:
            # partitioned map has N reduces; non-partitioned map has 1 reduce
            nr_reduces = get('partitions') or 1
        elif ispartitioned(input):
            # no map, with partitions: len(dir://) specifies nr_reduces
            nr_reduces = 1 + max(int(id)
                                 for dir in input
                                 for id, url in read_index(dir))
        else:
            # no map, without partitions can only have 1 reduce
            nr_reduces = 1

        if get('merge_partitions'):
            nr_reduces = 1

        return {'input': input,
                'worker': self.bin,
                'map?': has_map,
                'reduce?': has_reduce,
                'profile?': get('profile'),
                'nr_reduces': nr_reduces,
                'prefix': job.name,
                'scheduler': get('scheduler'),
                'owner': job.settings['DISCO_JOB_OWNER']}

    def jobenvs(self, job, **jobargs):
        """
        XXX
        """
        settings = job.settings
        settings['LC_ALL'] = 'C'
        settings['LD_LIBRARY_PATH'] = 'lib'
        settings['PYTHONPATH'] = ':'.join((settings.get('PYTHONPATH', ''), 'lib'))
        return settings.env

    def jobhome(self, job, **jobargs):
        """
        XXX
        """
        jobzip = self.jobzip(job, **jobargs)
        jobzip.close()
        return jobzip.dumps()

    def jobzip(self, job, **jobargs):
        """
        XXX
        """
        from clx import __file__ as clxpath
        from disco import __file__ as discopath
        from disco.fileutils import DiscoZipFile
        from disco.util import iskv
        def get(key):
            return self.getitem(key, job, **jobargs)
        jobzip = DiscoZipFile()
        jobzip.writepy(os.path.dirname(clxpath), 'lib')
        jobzip.writepy(os.path.dirname(discopath), 'lib')
        jobzip.writemodule(job.__module__)
        jobzip.writemodule(self.__module__)
        if isinstance(get('required_files'), dict):
            for path, bytes in get('required_files').iteritems():
                    jobzip.writebytes(path, bytes)
        else:
            for path in get('required_files'):
                jobzip.writepath(path)
        for mod in get('required_modules') or ():
            jobzip.writemodule((mod[0] if iskv(mod) else mod), 'lib')
        return jobzip

    def jobdata(self, job, **jobargs):
        """
        XXX
        """
        return cPickle.dumps((self, job, jobargs), -1)

    def start(self, task, job, **jobargs):
        from disco.sysutil import set_mem_limit
        set_mem_limit(job.settings['DISCO_WORKER_MAX_MEM'])
        if self.getitem('profile', job, **jobargs):
            from cProfile import runctx
            name = 'profile-%s' % task.uid
            path = task.path(name)
            runctx('self.run(task, job, **jobargs)', globals(), locals(), path)
            task.put(name, open(path).read())
        else:
            self.run(task, job, **jobargs)
        self.end(task, job, **jobargs)

    def run(self, task, job, **jobargs):
        """
        XXX
        """
        self[task.mode](task, job, **jobargs)

    def end(self, task, job, **jobargs):
        from disco.events import Status
        if not self['save'] or (task.mode == 'map' and self['reduce']):
            task.send()
            Status("Results sent to master").send()
        else:
            task.save()
            Status("Results saved to DDFS").send()

    @classmethod
    def unpack(cls, jobpack):
        try:
            from imp import find_module, load_module
            __disco__ = load_module('__disco__', *find_module('__main__', ['lib']))
            sys.modules['__main__'].__dict__.update(__disco__.__dict__)
        except ImportError:
            pass
        return cPickle.loads(jobpack.jobdata)

    @classmethod
    def main(cls):
        """
        XXX
        """
        from disco.error import DataError
        from disco.events import AnnouncePID, WorkerDone, DataUnavailable, TaskFailed
        from disco.job import JobPack
        from disco.task import Task
        from disco.fileutils import NonBlockingInput
        from disco.util import MessageWriter
        try:
            sys.stdin = NonBlockingInput(sys.stdin, timeout=600)
            sys.stdout = MessageWriter()
            AnnouncePID(str(os.getpid())).send()
            worker, job, jobargs = cls.unpack(JobPack.request())
            worker.start(Task.request(), job, **jobargs)
            WorkerDone().send()
        except (DataError, EnvironmentError, MemoryError), e:
            # check the number of open file descriptors (under proc), warn if close to max
            # http://stackoverflow.com/questions/899038/getting-the-highest-allocated-file-descriptor
            # also check for other known reasons for error, such as if disk is full
            DataUnavailable(traceback.format_exc()).send()
            raise
        except Exception, e:
            TaskFailed(MessageWriter.force_utf8(traceback.format_exc())).send()
            raise

if __name__ == '__main__':
    Worker.main()
