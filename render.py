from subprocess import Popen, PIPE
import tornado.template as T
import datetime

MAX_GPU_PERSON = 5
HOSTNAME = "cluster58"

class GPU(object):
    """Representation of a GPU"""
    def __init__(self, name, type='?', ngpu=0, ncpu=0, up=False):
        self.name = name # name of gpu, e.g. 'guppy5'
        self.type = type # type of gpu, e.g. 'txp'
        self.ngpu = ngpu # number of gpu on machine
        self.ncpu = ncpu # number of cpu on machine
        self.up = up # boolean, whether the node is up
        self.gpu_jobs = []
        self.cpu_jobs = []
    def add_job(self, job):
        if job.ngpu:
            # add it once per GPU used -- for plotting
            for i in range(job.ngpu):
                self.gpu_jobs.append(job)
        else:
            self.cpu_jobs.append(job)
    def gpu_used(self):
        """Returns number of gpus currently used"""
        return len(self.gpu_jobs)
    def gpu_free(self):
        """Returns number of gpus free"""
        return max(0, self.ngpu - len(self.gpu_jobs))
    def is_full(self):
        """Returns true iff all gpus are currently used"""
        return self.gpu_used() >= self.ngpu
    def __str__(self):
        return "%s: (%s) with %d gpus" % (self.name, self.type, self.ngpu)

class Job(object):
    """Representation of a job being run"""
    def __init__(self, user, node, ngpu, ncpu, time, int):
        self.user = user # user who launched job
        self.node = node # GPU object
        self.ngpu = ngpu # number of gpu used
        self.ncpu = ncpu # number of cpu used
        self.time = time # string representation of time taken
        self.int = int # bool interactive job
    def __str__(self):
        return "Job by %s on %s using %d gpu %d cpu" % (
                self.user.name, self.node.name, self.ngpu, self.ncpu)
    def tooltip(self):
        if self.is_long_interactive_job():
            return "This is a long-running interactive job!"
        return self.user.tooltip()
    def colour_class(self):
        """Return the css class (controlling colour) for the job"""
        if self.is_long_interactive_job():
            return "alert-danger"
        if self.user.is_overusing():
            return "alert-warning"
        return "alert-info"
    def is_long_interactive_job(self):
        """Returns true iff job is interactive, and has been running for
        at least 2 hours"""
        if not self.int:
            return False
        # if self.time is at least 8 characters long, the format is at least
        # "<optionally more>HH:MM:DD", so has been running for > 10 hours
        if len(self.time) > 7:
            return True
        # if self.time is less than 7 characters long, has not been running
        # for more than an hour
        if len(self.time) < 7:
            return False
        # now, self.time is exactly 7 charslong, check if first char is a "1"
        # if it is not a '1' it must be >= 2 hours
        return self.time[0] != '1'

class User(object):
    def __init__(self, name):
        self.name = name
        self.jobs = []
    def add_job(self, job):
        self.jobs.append(job)
    def gpu_used(self):
        return sum([job.ngpu for job in self.jobs])
    def is_overusing(self):
        return self.gpu_used() > MAX_GPU_PERSON
    def tooltip(self):
        if self.is_overusing():
            return "Using %d GPUs (too many!)" % self.gpu_used()
        return "Using %d GPUs" % self.gpu_used()

def query_cluster(cmd):
    command = "ssh "+HOSTNAME+" '"+cmd+"'"
    return Popen(command, shell=True, stdout=PIPE).stdout

def read_gpu_avail():
    # read
    gpus = {}
    for line in query_cluster('sinfo -o "%N\t%G\t%c\t%a\t%f\t%E" -h -N;'):
        name, gpu, cpu, status, type, msg = line.strip().split('\t')
        gpus[name] = GPU(name,
                         type,
                         ngpu=(0 if gpu == "(null)" else int(gpu.split(':')[1])),
                         ncpu=int(cpu),
                         up=(status == 'up')) 
    return gpus

def read_pty_bash_jobs():
    """
    Get the username and time submitted of --pty bash jobs.
    Unfortunately there is no way of getting this information from slurm
    """
    jobs = set()
    for line in query_cluster('ps -eo user,lstart,cmd | grep "pty bash" | grep "srun" | grep -v "grep"'):
        line = [x for x in line.strip().split(" ") if x]
        user = line[0]
        lstart = datetime.datetime.strptime(' '.join(line[1:6]),
                                            "%a %b %d %H:%M:%S %Y")
        jobs.add((user, lstart))
    return jobs

def read_jobs(gpus, interactive_jobs):
    # read
    users = {}
    jobs = []
    for line in query_cluster('squeue -o "%u\t%b\t%C\t%N\t%M\t%F\t%V\t%j" -h;'):
        username, gpu, cpu, gpuname, time, jobid, lstart, cmd = line.strip().split('\t')
        # convert time
        lstart = datetime.datetime.strptime(lstart, "%Y-%m-%dT%H:%M:%S")
        # sometimes the gpu list is incomplete, put in a placeholder for now
        if gpuname not in gpus:
            gpus[gpuname] = GPU(gpuname, up=False)
        if username not in users:
            users[username] = User(username)
        # create job object
        ngpu = 0
        try: 
            ngpu = int(gpu.split(':')[1])
        except:
            pass
        job = Job(users[username],
                  gpus[gpuname],
                  ngpu=ngpu,
                  ncpu=int(cpu),
                  time=time,
                  int=(cmd == "bash") and (username, lstart) in interactive_jobs)
        # append to jobs
        jobs.append(job)
        gpus[gpuname].add_job(job)
        users[username].add_job(job)
    return jobs, users

def render(gpus, jobs, users):
    # put UP gpu's first, then sort by gpu number e.g. guppy9 before guppy12
    gpus = sorted(gpus.values(), key=lambda gpu:
            (not gpu.up, int(gpu.name[5:] if gpu.name.startswith("guppy") else 0)))
    # usage stats
    total_gpus = sum([gpu.ngpu for gpu in gpus if gpu.up])
    total_used = sum([gpu.gpu_used() for gpu in gpus if gpu.up])
    # render template
    template = T.Template(open('src/template.html').read())
    output = template.generate(gpus=gpus,
                               jobs=jobs,
                               total_gpus=total_gpus,
                               total_used=total_used,
                               usage_rate=int(round(100 * total_used / total_gpus)),
                               update_time=datetime.datetime.now())
    open('index.html', 'w').write(output)

if __name__ == '__main__':
    interactive_jobs = read_pty_bash_jobs()

    gpus = read_gpu_avail() # from running Cluster Usage</h1>
    jobs, users = read_jobs(gpus, interactive_jobs)
    render(gpus, jobs, users)
