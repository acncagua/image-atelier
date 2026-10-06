"""Read NVIDIA identity and total/free memory, without inventing WDDM process memory."""
import csv
import io
import os
import subprocess
import time
import math
from urllib.parse import urlsplit

def local_server_instance(url):
    """Opaque local listener PID + creation time. No command line or process VRAM."""
    if os.name!='nt':return None
    try:
        import ctypes as ct
        import socket
        parsed=urlsplit(url);port=parsed.port
        if not port:return None
        class Row(ct.Structure):
            _fields_=[(key,ct.c_uint32) for key in ('state','local_addr','local_port','remote_addr','remote_port','pid')]
        class Row6(ct.Structure):
            _fields_=[('local_addr',ct.c_ubyte*16),('local_scope',ct.c_uint32),('local_port',ct.c_uint32),('remote_addr',ct.c_ubyte*16),*[(key,ct.c_uint32) for key in ('remote_scope','remote_port','state','pid')]]
        addresses={(a[0],a[4][0]) for a in socket.getaddrinfo(parsed.hostname,port,0,socket.SOCK_STREAM)};pids=set()
        api=ct.WinDLL('iphlpapi').GetExtendedTcpTable
        api.argtypes=[ct.c_void_p,ct.POINTER(ct.c_uint32),ct.c_int,ct.c_uint32,ct.c_uint32,ct.c_uint32];api.restype=ct.c_uint32
        for family,structure in ((socket.AF_INET,Row),(socket.AF_INET6,Row6)):
            expected={address for f,address in addresses if f==family}
            if not expected:continue
            size=ct.c_uint32(0);api(None,ct.byref(size),False,family,3,0)
            if not size.value:continue
            buffer=ct.create_string_buffer(size.value)
            if api(buffer,ct.byref(size),False,family,3,0):continue
            count=ct.c_uint32.from_buffer_copy(buffer).value
            for i in range(count):
                row=structure.from_buffer_copy(buffer,4+i*ct.sizeof(structure))
                if socket.ntohs(row.local_port&65535)!=port:continue
                raw=row.local_addr.to_bytes(4,'little') if family==socket.AF_INET else bytes(row.local_addr)
                address=socket.inet_ntop(family,raw)
                if address in expected or address in ('0.0.0.0','::'):pids.add(row.pid)
        if len(pids)!=1:return None
        pid=next(iter(pids))
        kernel=ct.WinDLL('kernel32');kernel.OpenProcess.argtypes=[ct.c_uint32,ct.c_bool,ct.c_uint32];kernel.OpenProcess.restype=ct.c_void_p
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None
        try:
            created=ct.c_uint64();exited=ct.c_uint64();kt=ct.c_uint64();ut=ct.c_uint64()
            kernel.GetProcessTimes.argtypes=[ct.c_void_p,*[ct.POINTER(ct.c_uint64)]*4];kernel.GetProcessTimes.restype=ct.c_bool
            if not kernel.GetProcessTimes(handle,ct.byref(created),ct.byref(exited),ct.byref(kt),ct.byref(ut)):return None
            return str(pid)+':'+str(created.value)
        finally:
            kernel.CloseHandle.argtypes=[ct.c_void_p];kernel.CloseHandle(handle)
    except (OSError,ValueError,AttributeError):return None

def inventory():
    try:
        env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('API_KEY','TOKEN','SECRET'))}
        r=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,name,pci.bus_id,memory.total,memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,check=True,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        values=[]
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row)!=6:continue
            index,uuid,name,pci,total,free=(v.strip() for v in row)
            total=float(total);free=float(free)
            if not math.isfinite(total) or not math.isfinite(free) or total<=0 or not 0<=free<=total:continue
            values.append({'index':int(index),'uuid':uuid,'name':name,'pci_bus':pci,'total_mib':total,'free_mib':free,'at':time.time(),'source':'nvidia-smi'})
        return values
    except (OSError,subprocess.SubprocessError,ValueError):return []

def verified_device(config,devices,comfy_stats,peer=None):
    if len(devices)!=1:raise ValueError('初期対応は同じPCの単一NVIDIA GPUです。GPUを一意に確認できません。')
    gpu=devices[0]
    if any(type(gpu.get(k)) not in (int,float) or not math.isfinite(gpu[k]) for k in ('total_mib','free_mib')) or gpu['total_mib']<=0 or not 0<=gpu['free_mib']<=gpu['total_mib']:raise ValueError('GPU容量・空きの実測を確認できません。')
    if config['gpu_uuid'] and gpu['uuid']!=config['gpu_uuid']:raise ValueError('指定したGPUが見つかりません。')
    entries=comfy_stats.get('devices',[])
    if len(entries)!=1 or entries[0].get('type')!='cuda' or entries[0].get('index')!=0 or gpu['name'] not in entries[0].get('name',''):
        raise ValueError('ComfyUIと対象GPUの一致を確認できません。')
    if peer and peer.get('state')=='loaded':
        if peer.get('gpu_name')!=gpu['name'] or type(peer.get('gpu_total_mib')) not in (int,float) or not math.isfinite(peer['gpu_total_mib']) or abs(peer['gpu_total_mib']-gpu['total_mib'])>128:
            raise ValueError('StrataとComfyUIが同じ物理GPUか確認できません。')
    if config['target_gib']*1024>gpu['total_mib']:raise ValueError('目標空きVRAMが対象GPUの容量を超えています。')
    return gpu
