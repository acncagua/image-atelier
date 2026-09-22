"""Small memory snapshots; no model load, network access or process changes."""
import os
import sys
import time

def memory_snapshot(pid=None):
    data={'time':time.time()}
    try:
        import psutil
        ram=psutil.virtual_memory()
        data.update(ram_total=ram.total,ram_available=ram.available)
        root=psutil.Process(pid or os.getpid());processes=[root]+root.children(recursive=True)
        data['processes']=[]
        for process in processes:
            try:
                info=process.memory_info()
                data['processes'].append({'pid':process.pid,'rss':info.rss,'private':getattr(info,'private',None),'vms':info.vms})
            except psutil.Error:pass
    except Exception as error:data['ram_error']=type(error).__name__
    if os.name=='nt':
        try:
            import ctypes as c
            class MEMORY(c.Structure):
                _fields_=[('length',c.c_uint32),('load',c.c_uint32)]+[(key,c.c_uint64) for key in ('total_physical','available_physical','commit_limit','commit_available','total_virtual','available_virtual','extended')]
            status=MEMORY();status.length=c.sizeof(status)
            call=c.WinDLL('kernel32',use_last_error=True).GlobalMemoryStatusEx
            call.argtypes=[c.POINTER(MEMORY)];call.restype=c.c_int
            if not call(c.byref(status)):raise c.WinError(c.get_last_error())
            data.update(windows_commit_limit=status.commit_limit,windows_commit_available=status.commit_available,process_virtual_available=status.available_virtual)
        except Exception as error:data['commit_error']=type(error).__name__
    torch=sys.modules.get('torch')
    if torch is not None:
        try:
            if torch.cuda.is_initialized():
                free,total=torch.cuda.mem_get_info()
                data['cuda']={'free':free,'total':total,'allocated':torch.cuda.memory_allocated(),'reserved':torch.cuda.memory_reserved(),'peak_allocated':torch.cuda.max_memory_allocated()}
        except Exception as error:data['cuda_error']=type(error).__name__
    return data
