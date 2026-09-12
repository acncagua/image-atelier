"""Windows 10+ atomic job assignment; never terminate a process by a saved PID.

The non-inheritable job handle belongs to the server. JOB_LIST attaches the
child during CreateProcess (there is no suspended, unowned launch interval).
Closing the last handle kills the entire tree, including venv launchers.
"""
import ctypes as c
import hashlib
import os
import subprocess
import time
from pathlib import Path


def job_name(directory):
    key=os.path.normcase(str(Path(directory).resolve())).encode('utf-8')
    return 'Local\\ImageAtelier-SwinIR-'+hashlib.sha256(key).hexdigest()


if os.name=='nt':
    from ctypes import wintypes as w
    dll=c.WinDLL('kernel32',use_last_error=True)
    SIZE=c.c_size_t
    class IO(c.Structure):
        _fields_=[(x,c.c_ulonglong) for x in ('read','write','other','read_bytes','write_bytes','other_bytes')]
    class BASIC(c.Structure):
        _fields_=[('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),
                  ('min_ws',SIZE),('max_ws',SIZE),('process_limit',w.DWORD),('affinity',SIZE),
                  ('priority',w.DWORD),('scheduling',w.DWORD)]
    class EXTENDED(c.Structure):
        _fields_=[('basic',BASIC),('io',IO),('process_memory',SIZE),('job_memory',SIZE),('peak_process',SIZE),('peak_job',SIZE)]
    class ACCOUNTING(c.Structure):
        _fields_=[(x,c.c_longlong) for x in ('user','kernel','period_user','period_kernel')]+[(x,w.DWORD) for x in ('faults','total','active','terminated')]
    class STARTUP(c.Structure):
        _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR)]+[(x,w.DWORD) for x in ('x','y','width','height','chars_x','chars_y','fill','flags')]+[('show',w.WORD),('reserved_size',w.WORD),('reserved_bytes',c.c_void_p),('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]
    class STARTUPEX(c.Structure):
        _fields_=[('startup',STARTUP),('attributes',c.c_void_p)]
    class PROCESS(c.Structure):
        _fields_=[('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]

    def api(name,args,result=w.BOOL):
        fn=getattr(dll,name);fn.argtypes=args;fn.restype=result;return fn
    create_job=api('CreateJobObjectW',[c.c_void_p,w.LPCWSTR],w.HANDLE)
    open_job=api('OpenJobObjectW',[w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE)
    close=api('CloseHandle',[w.HANDLE])
    set_job=api('SetInformationJobObject',[w.HANDLE,c.c_int,c.c_void_p,w.DWORD])
    query_job=api('QueryInformationJobObject',[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p])
    terminate_job=api('TerminateJobObject',[w.HANDLE,w.UINT])
    init_attrs=api('InitializeProcThreadAttributeList',[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(SIZE)])
    update_attr=api('UpdateProcThreadAttribute',[c.c_void_p,w.DWORD,SIZE,c.c_void_p,SIZE,c.c_void_p,c.c_void_p])
    delete_attrs=api('DeleteProcThreadAttributeList',[c.c_void_p],None)
    create_process=api('CreateProcessW',[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(STARTUPEX),c.POINTER(PROCESS)])
    get_exit=api('GetExitCodeProcess',[w.HANDLE,c.POINTER(w.DWORD)])
    open_process=api('OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE)
    wait_handle=api('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD)

    def checked(ok):
        if not ok:raise c.WinError(c.get_last_error())

    def active(handle):
        info=ACCOUNTING();checked(query_job(handle,1,c.byref(info),c.sizeof(info),None));return info.active

    def existing(directory):
        handle=open_job(0x0004|0x0008,False,job_name(directory))  # QUERY | TERMINATE
        if not handle and c.get_last_error()!=2:raise c.WinError(c.get_last_error())
        return handle


def tree_exited(directory):
    if os.name!='nt':return True
    handle=existing(directory)
    if not handle:return True
    try:return active(handle)==0
    finally:close(handle)


def reap_tree(directory):
    if os.name!='nt':return
    handle=existing(directory)
    if not handle:return
    try:
        checked(terminate_job(handle,1))
        deadline=time.monotonic()+10
        while active(handle):
            if time.monotonic()>deadline:raise RuntimeError('以前の推論プロセスの終了を確認できません。キューを再開しません。')
            time.sleep(.02)
    finally:close(handle)


def legacy_exited(pid):
    """Unknown legacy PID: probe only. A live/reused/inaccessible PID blocks recovery."""
    if not pid:return True
    if os.name!='nt':
        try:os.kill(pid,0)
        except ProcessLookupError:return True
        return False
    handle=open_process(0x00100000,False,int(pid))  # SYNCHRONIZE, no terminate right
    if not handle:return c.get_last_error()==87
    try:return wait_handle(handle,0)==0
    finally:close(handle)


class WindowsChild:
    def __init__(self,args,directory,cwd,env):
        self.job=None;self.handle=None;self.returncode=None
        self.job=create_job(None,job_name(directory));checked(self.job)
        try:
            if active(self.job):raise RuntimeError('同じジョブの推論プロセスがまだ動いています。')
            limits=EXTENDED();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
            checked(set_job(self.job,9,c.byref(limits),c.sizeof(limits)))
            size=SIZE();init_attrs(None,1,0,c.byref(size))
            buffer=c.create_string_buffer(size.value);checked(init_attrs(buffer,1,0,c.byref(size)))
            try:
                handles=(w.HANDLE*1)(self.job)
                checked(update_attr(buffer,0,0x0002000D,handles,c.sizeof(handles),None,None))
                startup=STARTUPEX();startup.startup.cb=c.sizeof(startup);startup.attributes=c.cast(buffer,c.c_void_p)
                info=PROCESS();command=c.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in args]))
                environment=c.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items(),key=lambda kv:kv[0].upper()))+'\0')
                checked(create_process(str(args[0]),command,None,None,False,0x08000000|0x00080000|0x00000400,
                                       environment,str(cwd),c.byref(startup),c.byref(info)))
                self.handle=info.process;self.pid=info.pid;close(info.thread)
            finally:delete_attrs(buffer)
        except BaseException:
            self.close();raise

    def poll(self):
        if self.returncode is None and active(self.job)==0:
            code=w.DWORD();checked(get_exit(self.handle,c.byref(code)));self.returncode=code.value
        return self.returncode

    def wait(self,timeout=None):
        deadline=time.monotonic()+timeout if timeout is not None else float('inf')
        while self.poll() is None:
            if time.monotonic()>deadline:raise subprocess.TimeoutExpired('SwinIR',timeout)
            time.sleep(.02)
        return self.returncode

    def kill(self):checked(terminate_job(self.job,1))

    def close(self):
        if self.job:close(self.job);self.job=None
        if self.handle:close(self.handle);self.handle=None


def launch(args,directory,cwd,env):
    if os.name=='nt':return WindowsChild(args,directory,cwd,env)
    return subprocess.Popen(args,cwd=cwd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
