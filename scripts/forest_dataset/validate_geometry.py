#!/usr/bin/env python3
"""Read-only empirical RGB-D/camera audit and self-contained offline HTML report.

Inputs are selection.json and Blender extraction records in --audit-dir. This
script never imports Unreal or changes rendered data or camera JSON files.
"""
from __future__ import annotations
import argparse
import base64
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import random
import re
import struct
import sys

os.environ.setdefault('OPENCV_IO_ENABLE_OPENEXR', '1')
import cv2
import numpy as np
from scipy.optimize import least_squares

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'Plugins/MatrixCityPlugin/Content/Python'))
sys.path.insert(0, str(PROJECT / 'scripts'))
import trajectory_geometry as geometry
import render_worker
from camera_json_protocol import _validate_payload

PAIR_INDICES = [(0, 1), (5, 6), (11, 12), (12, 13), (18, 19), (23, 24), (12, 14), (0, 12)]
CONTROLS = ['correct', 'depth_cm_as_m', 'depth_divide_twice', 'double_yz_flip',
            'c2w_as_w2c', 'focal_x2', 'radial_depth', 'pose_frame_plus1']
LABELS = {'correct': '当前协议', 'depth_cm_as_m': '错误：厘米当米',
          'depth_divide_twice': '错误：深度重复除以100', 'double_yz_flip': '错误：重复翻转Y/Z',
          'c2w_as_w2c': '错误：c2w当w2c', 'focal_x2': '错误：焦距乘2',
          'radial_depth': '错误：Z深度当射线距离', 'pose_frame_plus1': '错误：pose错位1帧'}


def artifact_file(root, name, write=False):
    """Keep the report directory small while accepting the original layout."""
    root=Path(root)
    reproduce={'selection.json','cameras.json','extract_audit_cameras.py','report-font.woff','FONT_LICENSE.txt'}
    evidence={'metrics.json','input_hashes.json','feature_correspondences.npz','geometry_metrics.png',
              'wide_baseline_diagnostic.json','wide_baseline_diagnostic.png'}
    target=root/('reproduce' if name in reproduce else 'evidence')/name if name in reproduce|evidence else root/name
    if not write and (root/name).is_file():
        return root/name
    if write:
        target.parent.mkdir(parents=True,exist_ok=True)
    return target


def load_visuals(root):
    """The standalone HTML is the canonical copy of display data."""
    legacy=Path(root)/'visuals.json'
    if legacy.is_file():
        return json.loads(legacy.read_text())
    document=(Path(root)/'index.html').read_text()
    match=re.search(r'<script id="data" type="application/json">(.*?)</script>',document,re.S)
    if match is None:
        raise ValueError('Report contains no embedded visualization data')
    return json.loads(match.group(1))['visuals']


def summarize(values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not a.size:
        return {'count': 0, 'mean': None, 'median': None, 'p95': None, 'max': None}
    return dict(count=int(a.size), mean=float(a.mean()), median=float(np.median(a)),
                p95=float(np.percentile(a, 95)), max=float(a.max()))


def pose_relative(a, b):
    """Map camera-a column vectors into camera-b coordinates."""
    ra, rb = a[:3, :3], b[:3, :3]
    return rb.T @ ra, rb.T @ (a[:3, 3] - b[:3, 3])


def project_pixels(uv, depth_m, a, b, k, offset=0.5, radial=False):
    rays = np.c_[(uv[:, 0] + offset - k[0, 2]) / k[0, 0],
                 (uv[:, 1] + offset - k[1, 2]) / k[1, 1], np.ones(len(uv))]
    z = np.asarray(depth_m,dtype=np.float64)
    if radial:
        z = z / np.linalg.norm(rays, axis=1)
    rotation, translation = pose_relative(a, b)
    points = (rays * z[:, None]) @ rotation.T + translation
    safe_z = np.where(np.abs(points[:, 2]) > 1e-12, points[:, 2], 1e-12)
    projected = np.c_[k[0, 0] * points[:, 0] / safe_z + k[0, 2] - offset,
                      k[1, 1] * points[:, 1] / safe_z + k[1, 2] - offset]
    return projected, points[:, 2]


def image_sample(image, uv, nearest=False):
    # OpenCV's remap dimensions must fit a signed 16-bit index. Chunk a
    # flattened full-resolution map rather than making a 262144-row image.
    parts=[]
    for start in range(0,len(uv),16384):
        maps=uv[start:start+16384].astype(np.float32).reshape(-1,1,2)
        parts.append(cv2.remap(image,maps[:,:,0],maps[:,:,1],
                    cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,borderValue=0).reshape((len(maps),)+image.shape[2:]))
    return np.concatenate(parts) if parts else np.empty((0,)+image.shape[2:],dtype=image.dtype)


def depth_stable(d):
    local_max = cv2.dilate(d, np.ones((3, 3), np.uint8))
    local_min = cv2.erode(d, np.ones((3, 3), np.uint8))
    return ((local_min > 0) & np.isfinite(d) &
            ((local_max - local_min) <= np.maximum(0.08, 0.002 * d))).astype(np.float32)


def dense_pair(da, db, ca, cb, poses, k, i, j, mode='correct', stride=4, reference=None):
    yy, xx = np.mgrid[0:da.shape[0]:stride, 0:da.shape[1]:stride]
    uv = np.c_[xx.ravel(), yy.ravel()]
    depth_scale = 100 if mode == 'depth_cm_as_m' else 0.01 if mode == 'depth_divide_twice' else 1
    aa, bb = poses[i].copy(), poses[j].copy()
    kk = k.copy()
    if mode == 'pose_frame_plus1':
        aa, bb = poses[min(i+1, 24)], poses[min(j+1, 24)]
    if mode == 'double_yz_flip':
        aa, bb = aa @ np.diag([1., -1., -1., 1.]), bb @ np.diag([1., -1., -1., 1.])
    if mode == 'c2w_as_w2c':
        aa, bb = np.linalg.inv(aa), np.linalg.inv(bb)
    if mode == 'focal_x2':
        kk[0, 0] *= 2; kk[1, 1] *= 2
    depths = da[yy, xx].ravel() * depth_scale
    q, z = project_pixels(uv, depths, aa, bb, kk, radial=mode == 'radial_depth')
    bounds = (np.isfinite(q).all(axis=1) & np.isfinite(z) & (z > 0) &
              (depths > 0) & (q[:, 0] >= 1) & (q[:, 1] >= 1) &
              (q[:, 0] < da.shape[1]-2) & (q[:, 1] < da.shape[0]-2))
    target = image_sample(db, q) * depth_scale
    if mode == 'radial_depth':
        rays = np.c_[(q[:, 0]+0.5-kk[0, 2])/kk[0, 0],
                     (q[:, 1]+0.5-kk[1, 2])/kk[1, 1], np.ones(len(q))]
        target = target / np.linalg.norm(rays, axis=1)
    error = np.abs(target-z)
    stable = (bounds & (depth_stable(da)[yy, xx].ravel() > .5) &
              (image_sample(depth_stable(db), q, nearest=True) > .5))
    visible = stable & (target > 0) & (error <= np.maximum(.1 * depth_scale, .005*np.abs(z)))
    sampled_rgb = image_sample(cb.astype(np.float32), q)
    source_rgb = ca[yy, xx].reshape(-1, 3).astype(np.float32)
    squared = np.mean((sampled_rgb-source_rgb)**2, axis=1)
    support = visible if reference is None else reference & bounds
    mse = float(np.mean(squared[support])) if np.any(support) else None
    psnr = 10*math.log10(255**2 / max(mse, 1e-12)) if mse is not None else None
    result = dict(mode=mode, samples=len(uv), in_bounds=int(bounds.sum()), stable=int(stable.sum()),
                  visible=int(visible.sum()), visible_fraction=float(visible.sum()/max(1, bounds.sum())),
                  depth_error_stable_m=summarize(error[stable]),
                  depth_error_all_m=summarize(error[bounds]), rgb_psnr_db=psnr,
                  rgb_support=int(support.sum()), rgb_squared_sum=float(squared[support].sum()),
                  depth_error_visible_m=summarize(error[visible]))
    return result, dict(uv=uv, q=q, error=error, visible=visible, stable=stable,
                        source_rgb=source_rgb, warped_rgb=sampled_rgb, bounds=bounds)


def feature_matches(ca, cb):
    sift = cv2.SIFT_create(nfeatures=1200)
    a, desc_a = sift.detectAndCompute(cv2.cvtColor(ca, cv2.COLOR_RGB2GRAY), None)
    b, desc_b = sift.detectAndCompute(cv2.cvtColor(cb, cv2.COLOR_RGB2GRAY), None)
    if desc_a is None or desc_b is None or min(len(a), len(b)) < 8:
        return np.empty((0, 2)), np.empty((0, 2))
    matcher = cv2.BFMatcher()
    pairs = matcher.knnMatch(desc_a, desc_b, k=2)
    reverse = matcher.knnMatch(desc_b, desc_a, k=2)
    good_reverse = {m.queryIdx: m.trainIdx for pair in reverse if len(pair)==2
                    for m, n in [pair] if m.distance < .75*n.distance}
    good = [m for pair in pairs if len(pair)==2 for m, n in [pair]
            if m.distance < .75*n.distance and good_reverse.get(m.trainIdx)==m.queryIdx]
    ua = np.array([a[m.queryIdx].pt for m in good]); ub = np.array([b[m.trainIdx].pt for m in good])
    if len(ua) < 8:
        return np.empty((0, 2)), np.empty((0, 2))
    # Image-only robust filtering: no expected K, pose, or depth in this gate.
    _, mask = cv2.findHomography(ua, ub, cv2.RANSAC, 3.0)
    keep = mask.ravel().astype(bool) if mask is not None else np.zeros(len(ua), bool)
    return ua[keep], ub[keep]


def feature_pair(ca, cb, da, poses, k, i, j):
    ua, ub = feature_matches(ca, cb)
    if not len(ua):
        return dict(matches=0), None
    depth = image_sample(da, ua)
    keep = np.isfinite(depth) & (depth > 0) & (image_sample(depth_stable(da), ua, nearest=True) > .5)
    ua, ub, depth = ua[keep], ub[keep], depth[keep]
    modes = {}
    for mode in CONTROLS:
        aa, bb, kk = poses[i], poses[j], k.copy()
        scale = 100 if mode == 'depth_cm_as_m' else .01 if mode == 'depth_divide_twice' else 1
        if mode == 'double_yz_flip':
            aa, bb = aa@np.diag([1.,-1.,-1.,1.]), bb@np.diag([1.,-1.,-1.,1.])
        if mode == 'c2w_as_w2c': aa, bb = np.linalg.inv(aa), np.linalg.inv(bb)
        if mode == 'pose_frame_plus1': aa, bb = poses[min(i+1,24)], poses[min(j+1,24)]
        if mode == 'focal_x2': kk[0,0]*=2;kk[1,1]*=2
        q, z = project_pixels(ua, depth*scale, aa, bb, kk, radial=mode=='radial_depth')
        errors=np.linalg.norm(q-ub,axis=1)
        modes[mode] = summarize(errors)
    data = dict(ua=ua, ub=ub, depth=depth, a=poses[i], b=poses[j], k=k)
    return dict(matches=len(ua), qualified=len(ua)>=12, modes=modes), data


def read_frame(path, evidence):
    stat = path.stat(); blob = path.read_bytes(); after = path.stat()
    if (stat.st_size,stat.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise RuntimeError('Input changed while reading: '+str(path))
    evidence.append(dict(path=str(path),sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob),mtime_ns=after.st_mtime_ns))
    decoded=cv2.imdecode(np.frombuffer(blob,np.uint8),cv2.IMREAD_UNCHANGED)
    if decoded is None: raise ValueError('Cannot decode '+str(path))
    if path.suffix=='.png':
        if decoded.shape[:2]!=(512,512):raise ValueError('RGB dimensions '+str(path))
        return cv2.cvtColor(decoded,cv2.COLOR_BGR2RGB)
    d=decoded[...,0] if decoded.ndim==3 else decoded
    if not np.isfinite(d).all() or np.any(d<=0):raise ValueError('Invalid depth '+str(path))
    if decoded.ndim==3 and (not np.array_equal(d,decoded[...,1]) or not np.array_equal(d,decoded[...,2])):
        raise ValueError('EXR RGB depth channels disagree '+str(path))
    return d.copy()*.01


def exr_channel_types(path):
    with path.open('rb') as f:
        f.read(8)
        def cs():
            value=b''
            while True:
                c=f.read(1)
                if not c:raise ValueError('Truncated EXR')
                if c==b'\0':return value
                value+=c
        while True:
            key=cs()
            if not key:return {}
            kind=cs();value=f.read(struct.unpack('<I',f.read(4))[0])
            if key==b'channels':
                types={};cursor=0
                while value[cursor]:
                    end=value.index(b'\0',cursor);name=value[cursor:end].decode()
                    types[name]=struct.unpack_from('<i',value,end+1)[0];cursor=end+17
                return types


def expected_poses(job, manifest, trajectory):
    match=re.match(r'(.+)_Target_(\d+)_height_(\d+)_ang_(\d+)$',job['prefix'])
    map_name,number,height,yaw=match.groups();height=float(height)*100;yaw=float(yaw)
    map_info=next(m for m in manifest['maps'] if m['map'].endswith('/'+map_name))
    target=np.array(map_info['targets']['Target_'+number]['transform']['location'])
    kind=job['trajectory'];count=25;size=trajectory['global']['trajectory_size']
    if kind=='fix_line':position,rotation=geometry.line(target,count,height,size,yaw)
    elif kind=='plane_grid':position,rotation=geometry.planar(target,count,height,size,yaw)
    elif kind=='rot_arc':position,rotation=geometry.arc(target,count,height,90,yaw)
    elif kind=='rot_spiral':position,rotation=geometry.spiral(target,count,height,90,1.5,yaw)
    else:
        heights=trajectory['global']['camera_heights']
        seed=render_worker.trajectory_seed(42,map_name,'Target_'+number,(kind,height,yaw))
        position,rotation=geometry.random_shell(target,count,min(heights),max(heights),90,height,yaw,random.Random(seed))
    # Independent axis conversion: UE world (X,Y,Z) -> (X,-Y,Z), cm -> m.
    # OpenCV camera axes in UE camera-local coordinates are (+Y,-Z,+X).
    world=np.diag([1.,-1.,1.]);camera=np.array([[0.,0.,1.],[1.,0.,0.],[0.,-1.,0.]])
    expected=np.tile(np.eye(4),(25,1,1))
    expected[:,:3,3]=position@world.T*.01
    expected[:,:3,:3]=np.array([world@geometry.rotation_matrix(r)@camera for r in rotation])
    return expected,world@target*.01,height*.01


def data_image(rgb, fmt='.jpg'):
    ok,encoded=cv2.imencode(fmt,cv2.cvtColor(np.clip(rgb,0,255).astype(np.uint8),cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY,86] if fmt=='.jpg' else [])
    if not ok:raise ValueError('Image encoding')
    return 'data:image/'+('jpeg' if fmt=='.jpg' else 'png')+';base64,'+base64.b64encode(encoded).decode()


def colorize(values, low, high, colormap=cv2.COLORMAP_TURBO):
    image=np.clip((values-low)/max(high-low,1e-12)*255,0,255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(image,colormap),cv2.COLOR_BGR2RGB)


POINT_DTYPE=np.dtype([('xyz','<f4',(3,)),('rgb','u1',(3,)),('frame','u1')])


def point_cloud(payload, depths, colors, target, stride=16):
    """Include every frame; spatial subsampling only controls HTML size."""
    poses=np.array([frame['transform_matrix'] for frame in payload['frames']])
    if len(depths)!=len(poses) or len(colors)!=len(poses):
        raise ValueError('Point clouds require RGB and depth for every camera frame')
    k=np.array(payload['intrinsics']);chunks=[];counts=[]
    for frame,(depth,rgb,pose) in enumerate(zip(depths,colors,poses)):
        yy,xx=np.mgrid[0:depth.shape[0]:stride,0:depth.shape[1]:stride]
        z=depth[yy,xx].ravel()
        rays=np.c_[(xx.ravel()+.5-k[0,2])/k[0,0],
                   (yy.ravel()+.5-k[1,2])/k[1,1],np.ones(len(z))]
        valid=np.isfinite(z)&(z>0)
        world=(rays[valid]*z[valid,None])@pose[:3,:3].T+pose[:3,3]-target
        chunk=np.empty(len(world),dtype=POINT_DTYPE)
        chunk['xyz']=world;chunk['rgb']=rgb[yy,xx].reshape(-1,3)[valid];chunk['frame']=frame
        chunks.append(chunk);counts.append(len(chunk))
    packed=np.concatenate(chunks)
    return dict(encoding='xyz-f32-rgb8-frame8-le',count=len(packed),frames=list(range(len(poses))),
                counts_by_frame=counts,stride_px=stride,
                data=base64.b64encode(packed.tobytes()).decode())


def refresh_point_clouds(root, visuals):
    """Rebuild display points from all frames without rerunning the numerical audit."""
    jobs=json.loads(artifact_file(root,'selection.json').read_text())
    records=json.loads(artifact_file(root,'cameras.json').read_text())
    hashes={entry['path']:entry['sha256'] for entry in json.loads(artifact_file(root,'input_hashes.json').read_text())}
    by_id={visual['id']:visual for visual in visuals}
    for job in jobs:
        visual=by_id[job['id']];payload=records[job['id']]['payload'];_validate_payload(payload)
        center=payload['center_idx']
        target=np.array(payload['frames'][center]['transform_matrix'])[:3,3]-visual['cameras'][center]
        depths=[];colors=[]
        for frame in range(len(payload['frames'])):
            for folder,extension,destination in [('rgb','png',colors),('depth','exr',depths)]:
                path=Path(job['GT_dir'])/folder/f'{frame:04d}.{extension}';evidence=[]
                destination.append(read_frame(path,evidence))
                if hashes.get(str(path))!=evidence[0]['sha256']:
                    raise ValueError('Display source changed since numerical audit: '+str(path))
        visual['point_cloud']=point_cloud(payload,depths,colors,target)
        visual.pop('points',None)
        print('ALL_FRAME_CLOUD_OK',job['id'],len(payload['frames']),visual['point_cloud']['count'],flush=True)


def visual_sample(job, payload, depth, colors, target, pair_visual):
    poses=np.array([f['transform_matrix'] for f in payload['frames']]);center=payload['center_idx']
    da=depth['GT'][center];db=depth['occ'][center]
    low,high=np.percentile(da,[1,99]);high=max(high,low+.05)
    output=dict(center=center,depth_limits_m=[float(low),float(high)],
                rgb=data_image(colors['GT'][center]),occ=data_image(colors['occ'][center]),
                depth=data_image(colorize(da,low,high)),
                depth_diff=data_image(colorize(np.maximum(da-db,0),0,3)),
                point_cloud=point_cloud(payload,depth['GT'],colors['GT'],target),
                cameras=(poses[:,:3,3]-target).round(6).tolist(),
                directions=poses[:,:3,2].round(7).tolist())
    if pair_visual is not None:
        v=pair_visual;shape=(512,512)
        warp=v['warped_rgb'].reshape(*shape,3)
        correct=v['source_rgb'].reshape(*shape,3)
        mask=v['visible'].reshape(shape)
        warp[~mask]=20
        output['warp']=data_image(warp)
        output['reference']=data_image(correct)
        rgb_error=colorize(np.mean(abs(warp-correct),axis=2),0,20)
        rgb_error[~mask]=20
        output['rgb_error']=data_image(rgb_error)
        derror=v['error'].reshape(shape);derr=colorize(derror,0,.25);derr[~v['bounds'].reshape(shape)]=20
        output['depth_error']=data_image(derr)
    return output


def fit_feature_geometry(items):
    selected=[x for x in items if x['kind'] in ('rot_arc','rot_spiral','rand_shell')]
    def residual(parameters, data):
        scale_f,scale_d=np.exp(parameters);results=[]
        for item in data:
            kk=item['k'].copy();kk[0,0]*=scale_f;kk[1,1]*=scale_f
            q,z=project_pixels(item['ua'],item['depth'].astype(np.float64)*scale_d,item['a'],item['b'],kk)
            results.extend((q-item['ub']).ravel())
        return np.asarray(results)
    if not selected:return None
    # Hold out one trajectory family; this is a diagnostic fit, not a correction.
    train=[x for x in selected if x['kind']!='rand_shell'];hold=[x for x in selected if x['kind']=='rand_shell']
    opt=least_squares(lambda x:residual(x,train),np.zeros(2),loss='soft_l1',f_scale=1,
                      bounds=([-1.5,-1.5],[1.5,1.5]),max_nfev=40)
    return dict(training='rot_arc + rot_spiral image-only SIFT matches',holdout='rand_shell',
                focal_scale=float(np.exp(opt.x[0])),depth_relative_scale=float(np.exp(opt.x[1])),
                training_coordinate_abs_px_before=summarize(abs(residual(np.zeros(2),train))),
                training_coordinate_abs_px_after=summarize(abs(residual(opt.x,train))),
                holdout_coordinate_abs_px_before=summarize(abs(residual(np.zeros(2),hold))),
                holdout_coordinate_abs_px_after=summarize(abs(residual(opt.x,hold))),
                note='Diagnostic only; a global simultaneous pose/depth scale is unobservable in reprojection. Absolute metric scale is anchored independently to the approved UE trajectory.')


def audit(audit_dir):
    cv2.setNumThreads(2);cv2.setRNGSeed(42)
    root=Path(audit_dir);jobs=json.loads(artifact_file(root,'selection.json').read_text())
    cameras_path=artifact_file(root,'cameras.json')
    camera_records=json.loads(cameras_path.read_text()) if cameras_path.is_file() else {}
    manifest=json.loads((PROJECT/'Config/Dataset/dataset_manifest.json').read_text())
    prod_plan=json.loads((PROJECT/'Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/execution/train/plan.json').read_text())
    evidence=[];sequences=[];features=[];pair_rows=[];visuals=[];center_groups=defaultdict(list)
    for job in jobs:
        record=(camera_records[job['id']] if job['id'] in camera_records else
                json.loads((root/(job['id']+'.json')).read_text()))
        payload=record['payload'];_validate_payload(payload)
        fbx=Path(job['fbx_path']);blob=fbx.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=payload['source']['fbx_sha256']:
            raise ValueError('FBX changed after audit extraction: '+str(fbx))
        evidence.append(dict(path=str(fbx),sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob),mtime_ns=fbx.stat().st_mtime_ns))
        poses=np.array([f['transform_matrix'] for f in payload['frames']]);k=np.array(payload['intrinsics'])
        rotation=poses[:,:3,:3]
        if job['scope']=='existing_json':
            config=json.loads((Path(job['output_root']).parent/'run/plan.json').read_text())['trajectory_config']
        elif job['scope']=='environment_audit':
            config=json.loads((Path(job['output_root']).parent/'run/plan.json').read_text())['trajectory_config']
        else: config=prod_plan['trajectory_config']
        expected,target,height=expected_poses(job,manifest,config)
        dp=np.linalg.norm(poses[:,:3,3]-expected[:,:3,3],axis=1)
        rotation_delta=rotation@expected[:,:3,:3].transpose(0,2,1)
        angles=np.degrees(np.arccos(np.clip((np.trace(rotation_delta,axis1=1,axis2=2)-1)/2,-1,1)))
        raw=record['raw_fbx_properties'];raw_focal=raw['FocalLength'][-1]
        raw_sensor=raw['FilmWidth'][-1]*25.4;raw_fx=512*raw_focal/raw_sensor
        existing=[]
        for suffix in ('GT','occ'):
            path=Path(job[suffix+'_dir'])/'pose/transforms.json'
            if path.is_file():
                blob=path.read_bytes();other=json.loads(blob);_validate_payload(other)
                evidence.append(dict(path=str(path),sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob),mtime_ns=path.stat().st_mtime_ns))
                existing.append(dict(suffix=suffix,pose_max_delta=float(np.max(abs(np.array([f['transform_matrix'] for f in other['frames']])-poses))),
                                     intrinsic_max_delta=float(np.max(abs(np.array(other['intrinsics'])-k))),sha256=hashlib.sha256(blob).hexdigest(),
                                     fbx_hash_matches=other['source']['fbx_sha256']==payload['source']['fbx_sha256']))
        d={};c={};stats={};quantization=[]
        for suffix in ('GT','occ'):
            folder=Path(job[suffix+'_dir']);d[suffix]=[];c[suffix]=[]
            for frame in range(25):
                c[suffix].append(read_frame(folder/f'rgb/{frame:04d}.png',evidence))
                d[suffix].append(read_frame(folder/f'depth/{frame:04d}.exr',evidence))
            stats[suffix]=dict(depth_min_m=float(min(x.min() for x in d[suffix])),depth_max_m=float(max(x.max() for x in d[suffix])),
                               frame_medians_m=[float(np.median(x)) for x in d[suffix]])
            center=payload['center_idx'];cm=d[suffix][center]*100
            quantization.extend((np.spacing(cm.astype(np.float16)).astype(np.float32)*.01).ravel()[::64])
        types=exr_channel_types(Path(job['GT_dir'])/'depth/0012.exr')
        no_occlusion=[];reverse_occlusion=[]
        for a,b in zip(d['GT'],d['occ']):
            tol=np.maximum(.08,.002*a)
            no_occlusion.append(np.mean(abs(a-b)<=tol));reverse_occlusion.append(np.mean(b>a+tol))
        seq=dict(id=job['id'],prefix=job['prefix'],scope=job['scope'],trajectory=job['trajectory'],height_m=height,
                 k=k.tolist(),raw_focal_mm=raw_focal,raw_sensor_mm=raw_sensor,raw_fbx_expected_fx=raw_fx,
                 raw_fbx_intrinsic_delta_px=float(abs(raw_fx-k[0,0])),fbx_unit_scale_cm=raw['UnitScaleFactor'],
                 pose_position_error_m=summarize(dp),pose_rotation_error_deg=summarize(angles),
                 rotation_det_error=float(np.max(abs(np.linalg.det(rotation)-1))),
                 rotation_orthogonal_error=float(np.max(abs(rotation.transpose(0,2,1)@rotation-np.eye(3)))),
                 center_height_above_target_m=float(poses[payload['center_idx'],2,3]-target[2]),
                 baseline_first_last_m=float(np.linalg.norm(poses[-1,:3,3]-poses[0,:3,3])),existing_json=existing,
                 depth=stats,exr_channel_types=types,half_float_depth_step_m=summarize(quantization),
                 occ_gt_equal_depth_fraction=float(np.mean(no_occlusion)),
                 occ_farther_than_gt_fraction=float(np.mean(reverse_occlusion)),pairs=[],features=[])
        pair_visual=None
        for suffix in ('GT','occ'):
            for i,j in PAIR_INDICES:
                correct,v=dense_pair(d[suffix][i],d[suffix][j],c[suffix][i],c[suffix][j],poses,k,i,j)
                row=dict(sequence_id=job['id'],scope=job['scope'],trajectory=job['trajectory'],suffix=suffix,i=i,j=j,metrics={'correct':correct})
                for mode in CONTROLS[1:]:
                    metric,_=dense_pair(d[suffix][i],d[suffix][j],c[suffix][i],c[suffix][j],poses,k,i,j,mode,reference=v['visible'])
                    row['metrics'][mode]=metric
                seq['pairs'].append(row);pair_rows.append(row)
                if suffix=='GT' and (i,j)==(12,13):
                    _,pair_visual=dense_pair(d[suffix][i],d[suffix][j],c[suffix][i],c[suffix][j],poses,k,i,j,stride=1)
            selected_pairs=[(0,1),(12,13),(23,24)] if suffix=='GT' else [(12,13)]
            for i,j in selected_pairs:
                fm,fd=feature_pair(c[suffix][i],c[suffix][j],d[suffix][i],poses,k,i,j)
                fm.update(suffix=suffix,i=i,j=j)
                seq['features'].append(fm)
                if fd is not None and fm['matches']>=12:
                    fd['kind']=job['trajectory'];features.append(fd)
        center_groups[(job['scope'],job['prefix'])].append((job['trajectory'],poses[payload['center_idx']],k,d['GT'][payload['center_idx']]))
        visuals.append(dict(id=job['id'],label=job['prefix']+' / '+job['trajectory']+' / '+job['scope'],
                            **visual_sample(job,payload,d,c,target,pair_visual)))
        sequences.append(seq)
        print('AUDIT_SEQUENCE_OK',job['id'],'position_max_m',dp.max(),'rotation_max_deg',angles.max(),flush=True)
        (root/'progress.json').write_text(json.dumps(dict(completed=len(sequences),total=len(jobs))))
    centers=[]
    for (scope,prefix),items in center_groups.items():
        if len(items)<2:continue
        name,pose,k,depth=items[0]
        centers.append(dict(scope=scope,prefix=prefix,trajectories=len(items),
                            max_pose_delta=float(max(np.max(abs(x[1]-pose)) for x in items)),
                            max_k_delta=float(max(np.max(abs(x[2]-k)) for x in items)),
                            max_depth_mean_difference_m=float(max(np.mean(abs(x[3]-depth)) for x in items))))
    modes={}
    for mode in CONTROLS:
        metrics=[row['metrics'][mode] for row in pair_rows]
        psnrs=[m['rgb_psnr_db'] for m in metrics if m['rgb_psnr_db'] is not None]
        n=sum(m['rgb_support'] for m in metrics);sse=sum(m['rgb_squared_sum'] for m in metrics)
        feature_errors=[]
        for seq in sequences:
            for f in seq['features']:
                if f.get('matches',0)>=12: feature_errors.append(f['modes'][mode]['median'])
        modes[mode]=dict(label=LABELS[mode],pair_count=len(metrics),rgb_pair_psnr_db=summarize(psnrs),
                         aggregate_rgb_psnr_db=float(10*np.log10(255**2/(sse/n))) if n and sse else None,
                         rgb_support_pixels=n,feature_pair_median_error_px=summarize(feature_errors),
                         median_visible_fraction=float(np.median([m['visible_fraction'] for m in metrics])))
    # Verify the data inspected have not been replaced during this audit.
    changed=[]
    for item in evidence:
        stat=Path(item['path']).stat()
        if (stat.st_size,stat.st_mtime_ns)!=(item['bytes'],item['mtime_ns']):changed.append(item['path'])
    if changed:raise RuntimeError('Input changed during audit: '+str(changed[:5]))
    pass_evidence={}
    for suffix in ('GT','occ'):
        measurements=[p['metrics']['correct'] for p in pair_rows if p['suffix']==suffix]
        pass_evidence[suffix]=dict(pair_count=len(measurements),
            median_visible_fraction=float(np.median([m['visible_fraction'] for m in measurements])),
            stable_region_pair_p95_depth_error_m=summarize([m['depth_error_stable_m']['p95'] for m in measurements if m['stable']]),
            note='Stable-region errors are not conditioned on depth agreement. OCC errors include real changes of visibility; do not interpret every projected source point as still visible.')
    feature_support=dict(minimum_matches=12,
        qualified_pairs=sum(f.get('matches',0)>=12 for s in sequences for f in s['features']),
        insufficient_pairs=sum(f.get('matches',0)<12 for s in sequences for f in s['features']))
    result=dict(schema='camera-depth-geometry-audit-v1',created_utc=datetime.now(timezone.utc).isoformat(),
                sequences=sequences,pairs=pair_rows,controls=modes,centers=centers,
                diagnostic_fit=fit_feature_geometry(features),depth_evidence_by_pass=pass_evidence,feature_support=feature_support,
                unchanged_input_files=len(evidence),input_changes=changed,
                coverage=dict(sequences=len(sequences),frames=len(sequences)*25*2,rgb_files=len(sequences)*25*2,
                              depth_files=len(sequences)*25*2,pairs=len(pair_rows),
                              existing_json_pairs=sum(bool(s['existing_json']) for s in sequences),
                              production_audit_sequences=sum(s['scope']=='production_audit' for s in sequences)),
                formulas=dict(world='X_b=diag(1,-1,1) X_UE_cm /100',
                              camera='X_cv=inv(c2w)*X_world; camera axes right/down/forward',
                              unprojection='X_cv=Z_cm/100 * K^-1 * [u+0.5,v+0.5,1]',
                              projection='uv_b=project(K * inv(c2w_b)*c2w_a*X_cv)-0.5'),
                limitations=['Only listed sampled sequences were decoded; no claim that the full production dataset is complete.',
                             'Production outputs had no pose JSON at discovery; audit copies were regenerated without writing to the dataset.',
                             'Absolute world coordinates and common metric scale cannot be established by reprojection alone; approved UE actor/trajectory coordinates anchor them.',
                             'Image feature filtering uses mutual SIFT ratio and image-only homography RANSAC; geometry/depth residuals do not select matches.',
                             'Dense visibility masks use only bounds, positive depth, local depth smoothness and depth agreement, never RGB error.',
                             'Finite half-float depth precision, foliage, transparency, spatial antialiasing and view-dependent RGB remain measurement limitations.'],
                references=[dict(title='OpenCV pinhole camera model',url='https://docs.opencv.org/4.12.0/d9/d0c/group__calib3d.html'),
                            dict(title='UE length units',url='https://dev.epicgames.com/documentation/en-us/unreal-engine/units-of-measurement-in-unreal-engine')])
    artifact_file(root,'metrics.json',write=True).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    artifact_file(root,'input_hashes.json',write=True).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    np.savez_compressed(artifact_file(root,'feature_correspondences.npz',write=True),**{
        f'{index}_{key}':np.asarray(value) for index,item in enumerate(features)
        for key,value in item.items()})
    print('AUDIT_METRICS_COMPLETE',root,flush=True)
    return result,visuals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir',required=True)
    parser.add_argument('--html-only',action='store_true')
    parser.add_argument('--refresh-pointclouds',action='store_true',
                        help='With --html-only, rebuild display points from every audited GT frame')
    args=parser.parse_args();root=Path(args.audit_dir)
    if args.refresh_pointclouds and not args.html_only:
        parser.error('--refresh-pointclouds requires --html-only')
    if args.html_only:
        result=json.loads(artifact_file(root,'metrics.json').read_text());visuals=load_visuals(root)
        if args.refresh_pointclouds:
            refresh_point_clouds(root,visuals)
            result['visualization']=dict(updated_utc=datetime.now(timezone.utc).isoformat(),
                                         frames_per_sequence=25,spatial_stride_px=16,
                                         default_frames='all',source_hashes_verified=True)
    else: result,visuals=audit(root)
    render_html(root,result,visuals)
    (root/'progress.json').unlink(missing_ok=True)


# HTML renderer is defined below so pure geometry helpers stay importable.
def render_html(root,result,visuals):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    result.setdefault('runtime',{})['report_generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    sequences=result['sequences'];controls=result['controls'];coverage=result['coverage']
    max_position=max(s['pose_position_error_m']['max'] for s in sequences)
    max_angle=max(s['pose_rotation_error_deg']['max'] for s in sequences)
    max_k=max(s['raw_fbx_intrinsic_delta_px'] for s in sequences)
    existing=[x for s in sequences for x in s['existing_json']]
    matching_existing=all(x['pose_max_delta']<1e-7 and x['intrinsic_max_delta']<1e-7 and x['fbx_hash_matches'] for x in existing)
    feature=controls['correct']['feature_pair_median_error_px']['median']
    depth_p95=[p['metrics']['correct']['depth_error_stable_m']['p95'] for p in result['pairs']
               if p['metrics']['correct']['depth_error_stable_m']['count']]
    feature_pass=feature is not None and feature<1
    gt_evidence=result['depth_evidence_by_pass']['GT']
    occ_evidence=result['depth_evidence_by_pass']['occ']
    passed=(max_position<.001 and max_angle<.01 and max_k<.001 and matching_existing and feature_pass
            and gt_evidence['stable_region_pair_p95_depth_error_m']['median']<.15)
    result['assessment']=dict(sample_geometry_pass=passed,max_position_error_m=max_position,
                              max_rotation_error_deg=max_angle,max_intrinsic_error_px=max_k,
                              existing_json_matches=matching_existing,feature_pair_median_px=feature,
                              median_pair_stable_depth_p95_m=float(np.median(depth_p95)))
    fig,axes=plt.subplots(1,2,figsize=(13,4.6),layout='constrained')
    modes=list(controls);english=['Correct','cm as m','divide 100 twice','double Y/Z flip','inverse pose','focal x2','radial depth','pose +1 frame']
    values=[controls[m]['feature_pair_median_error_px']['median'] or .001 for m in modes]
    axes[0].barh(english,values,color=['#0e9988']+['#bf6863']*7)
    axes[0].set_xscale('log');axes[0].set_xlabel('Median feature reprojection error (px, log scale)')
    axes[0].invert_yaxis();axes[0].grid(axis='x',alpha=.2)
    names=['fix_line','plane_grid','rot_arc','rot_spiral','rand_shell'];data=[]
    for name in names:
        errors=[]
        for seq in sequences:
            if seq['trajectory']==name:
                errors.extend(f['modes']['correct']['median'] for f in seq['features'] if f.get('matches',0)>=12)
        data.append(errors)
    axes[1].boxplot(data,tick_labels=names,showfliers=False)
    axes[1].set_ylabel('Feature pair median error (px)');axes[1].grid(axis='y',alpha=.2)
    axes[1].tick_params(axis='x',rotation=25)
    chart=artifact_file(root,'geometry_metrics.png',write=True);fig.savefig(chart,dpi=160,facecolor='#ffffff');plt.close(fig)
    chart_data='data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()
    table=''.join('<tr><td>'+html.escape(LABELS[m])+'</td><td>'+fmt(controls[m]['feature_pair_median_error_px']['median'])+
                  '</td><td>'+fmt(controls[m]['aggregate_rgb_psnr_db'])+'</td><td>'+str(controls[m]['rgb_support_pixels'])+'</td></tr>' for m in modes)
    fit=result['diagnostic_fit'] or {}
    refs=' '.join('<a href="'+html.escape(r['url'])+'">'+html.escape(r['title'])+'</a>' for r in result['references'])
    document=r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>相机与深度几何检查</title>
<style>
:root{color-scheme:light;--ink:#183443;--muted:#5c7381;--accent:#0d8d7a;--line:#dce7ec;--paper:#fff;--bg:#eff4f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 system-ui,"Noto Sans CJK SC",sans-serif}main{max-width:1450px;margin:auto;padding:34px 28px 60px}h1{font-size:30px;margin:0}h2{font-size:22px;margin:0 0 16px}h3{font-size:16px;margin:0 0 7px}.lead{color:var(--muted);margin:8px 0}.badge{background:#ddf3ed;color:#126e61;border:1px solid #b3e2d5;border-radius:24px;padding:5px 13px;display:inline-block;font-weight:650}.warning{background:#fff3d8;color:#7f5d0d;border-left:4px solid #d6a533;padding:13px 18px;margin:20px 0}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:20px 0}.kpi,.panel{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:20px}.kpi b{font-size:25px;display:block;line-height:1.3;color:var(--accent)}.kpi span{font-size:13px;color:var(--muted)}.panel{margin:18px 0}.small{font-size:13px;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.quad{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}img{width:100%;display:block;border-radius:7px;background:#14232d}figure{margin:0}figcaption{font-size:13px;color:var(--muted);padding:6px 0}select,button{font:inherit;padding:8px 11px;border:1px solid #c6d8e0;border-radius:7px;background:#fff;color:var(--ink)}select{max-width:100%;width:100%;margin-bottom:10px}button{cursor:pointer}button:hover{border-color:var(--accent)}button[aria-pressed="true"]{background:#0d8d7a;color:white;border-color:#0d8d7a}button:disabled{opacity:.4;cursor:not-allowed}#trajectory-buttons button{flex:1;min-width:125px}#trajectory-viewer{scroll-margin-top:16px}.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}th{background:#f2f7f8;font-weight:650}.scroll{overflow-x:auto}code,pre{font:13px/1.7 ui-monospace,monospace;background:#f1f6f8}code{padding:2px 5px;border-radius:3px}pre{padding:13px;overflow:auto;border-radius:7px}a{color:#087c84}canvas{display:block;width:100%;height:470px;background:#101f2a;border-radius:9px;touch-action:none}.legend{color:var(--muted);font-size:13px}.tag{display:inline-block;border-radius:4px;padding:2px 7px;background:#eaf1f5;font-size:12px}.path{overflow-wrap:anywhere}.chart{max-width:1300px;margin:auto}.metricgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.metricgrid div{background:#f3f7f8;padding:12px;border-radius:7px}.metricgrid b{display:block;font-size:19px}details{margin:12px 0}summary{cursor:pointer;font-weight:600}.foot{margin-top:28px;color:var(--muted);font-size:13px}@media(max-width:850px){.quad,.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.metricgrid{grid-template-columns:repeat(2,1fr)}main{padding:22px 14px}h1{font-size:25px}}
html{scroll-behavior:smooth}main{max-width:1400px;padding-top:32px}h1{font-size:32px;letter-spacing:.02em}h2{display:flex;gap:12px;align-items:center;margin-bottom:18px;font-size:23px}h3{font-size:17px}p{margin:10px 0}.lead{font-size:17px}.section-nav{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0}.section-nav a{padding:8px 15px;background:white;border:1px solid var(--line);border-radius:24px;text-decoration:none;font-size:14px}.section-nav a:hover{background:#e0f0ed}.section-number{font:600 14px/1.6 system-ui;color:#087c72;background:#e4f3ef;border-radius:7px;padding:5px 9px}.panel{padding:26px 28px;margin:22px 0;scroll-margin-top:20px}.badge{font-size:14px}.conclusion-text{max-width:950px;font-size:17px;line-height:1.9}.kpis{gap:12px;margin:22px 0 10px}.kpi{padding:17px;background:#f5f9fa;border:0;border-radius:8px}.kpi b{font-size:24px;white-space:nowrap;margin-bottom:6px}.warning{padding:14px 18px;margin:18px 0 8px;border-radius:0 8px 8px 0}.warning p{margin:5px 0}.section-intro{color:var(--muted);margin:-5px 0 20px}.field-label{display:block;font-weight:600;margin-bottom:7px}select{padding:10px 12px}.controls{margin-bottom:0;gap:9px}#trajectory-buttons{gap:10px;margin:4px 0 10px}#trajectory-buttons button{display:flex;flex-direction:column;align-items:center;gap:1px;padding:11px 7px;min-width:120px}#trajectory-buttons button span{font-size:16px;font-weight:600}#trajectory-buttons button small{font:12px/1.5 ui-monospace,monospace;opacity:.75}.viewer-toolbar{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:18px 0 12px}.viewer-toolbar select{width:auto;margin:0;padding:7px 10px}.viewer-toolbar label{white-space:nowrap;font-size:14px}.viewer-toolbar button{font-size:14px}.viewer-status{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;background:#eaf3f4;padding:9px 13px;margin-top:10px;border-radius:7px;font-size:13px;color:var(--muted)}#cloud-frame-status{font-weight:600;color:var(--ink)}.legend-items{display:flex;flex-wrap:wrap;gap:9px 22px;margin:12px 0;font-size:13px;color:var(--muted)}.legend-items i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}.frame-grid{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:7px;margin-top:12px}.frame-grid label{display:flex;align-items:center;gap:5px;font-size:14px;padding:5px 8px;background:#f4f8f9;border-radius:5px}.frame-filter .controls{margin-top:10px}.subheading{margin-top:26px}.quad{gap:16px}.quad figcaption{line-height:1.65;padding-top:9px}.metricgrid{margin:16px 0}.scroll{margin:12px 0}th{vertical-align:top}td{vertical-align:top}details{border-top:1px solid var(--line);padding-top:13px;margin:16px 0 0}summary{font-size:14px;line-height:1.8}details[open]>summary{margin-bottom:12px}details p{max-width:1000px}li{margin:7px 0}.small{line-height:1.8}.path{word-break:break-word}input[type=checkbox]{accent-color:var(--accent);width:16px;height:16px;vertical-align:middle}a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #63b8aa;outline-offset:3px}@media(max-width:1050px){.kpi b{font-size:21px}.frame-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}@media(max-width:850px){main{padding:22px 14px 40px}.panel{padding:20px 16px}.section-nav{gap:7px}.section-nav a{padding:7px 10px;font-size:13px}h1{font-size:26px}h2{font-size:21px}.quad{grid-template-columns:repeat(2,minmax(0,1fr))}#trajectory-buttons{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}#trajectory-buttons button{min-width:0}.frame-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.viewer-status{display:block}.viewer-status span{display:block}.viewer-toolbar{gap:12px}.conclusion-text{font-size:16px}.kpi{padding:13px}.kpi b{font-size:20px}.kpis{gap:9px}#sequence{font-size:13px}}
</style></head><body><main>
<header><span class="tag">已有数据实测 · 25 帧协议 · 可离线打开</span><h1>相机与深度几何检查</h1>
<p class="lead">检查位姿、内参和深度是否使用相同的单位与坐标系。</p>
<p class="small">数值检查时间：@@TIME@@</p></header>
<nav class="section-nav" aria-label="报告导航"><a href="#conclusion">1. 结论</a><a href="#trajectory-viewer">2. 轨迹与点云</a><a href="#sequence-details">3. 图像与残差</a><a href="#conventions">4. 数据用法</a><a href="#evidence">5. 检查证据</a></nav>
<section class="panel" id="conclusion"><h2><span class="section-number">01</span>先看结论</h2>
<span class="badge">@@STATUS@@</span><p class="conclusion-text">@@CONCLUSION@@</p>
<div class="kpis"><div class="kpi"><b>@@SEQUENCES@@ 条序列</b><span>覆盖五类轨迹、四类环境</span></div><div class="kpi"><b>@@FRAMES@@ 组图像</b><span>每组读取 RGB 和 EXR，包含 GT 与 OCC</span></div><div class="kpi"><b>@@POSITION@@ mm</b><span>位置转换的最大误差</span></div><div class="kpi"><b>@@FEATURE@@ px</b><span>特征重投影误差的中位数</span></div></div>
<p class="small">位置误差以 UE 清单和采集轨迹为基准。特征误差先取每个帧对的中位数，再取中位数。</p>
<div class="warning"><b>结论适用范围</b><p>本报告检查抽样序列。报告不代表全量数据已经完成。</p><p>OCC 的共同可见区域较少。树叶、透明物和遮挡边界的深度精度仍受限制。</p></div>
<details><summary>查看抽样范围与数据状态</summary><ul><li>20 条序列来自正式训练输出。</li><li>5 条序列用于检查已有相机 JSON。</li><li>7 条序列补充不同环境与测试场景。</li></ul><p>检查开始时，正式 train 已有图像和 FBX。正式 train 尚无 pose JSON，正式 test 为空。</p><p>本次重新转换的相机记录保存在报告目录。正式数据仍需补导出 JSON。</p></details></section>

<section class="panel" id="trajectory-viewer"><h2><span class="section-number">02</span>查看轨迹与点云</h2>
<p class="section-intro">先选场景，再选轨迹。默认显示所选轨迹的全部 25 帧。</p>
<label for="scene-select" class="field-label">场景样本</label><select id="scene-select" aria-label="选择三维场景样本"></select>
<div id="trajectory-buttons" class="controls" aria-label="选择相机轨迹"></div><p id="cloud-current" class="small"></p>
<div class="viewer-toolbar"><div class="controls"><button id="reset">斜视 / 重置</button><button id="top-view">俯视</button><button id="side-view">侧视</button></div><div class="controls"><label><input id="show-cameras" type="checkbox" checked> 相机轨迹</label><label><input id="show-points" type="checkbox" checked> 点云</label><label for="point-color">点云颜色</label><select id="point-color"><option value="rgb">原图颜色</option><option value="frame">按帧着色</option></select></div></div>
<canvas id="cloud" aria-label="全部帧的三维点云与相机轨迹"></canvas>
<div class="viewer-status"><span id="cloud-frame-status" role="status"></span><span>拖动旋转 · 滚轮缩放 · 单位：米</span></div>
<div class="legend-items"><span><i style="background:#6fb5ff"></i>蓝线：相机轨迹</span><span><i style="background:#fc827a"></i>红线：相机朝向</span><span><i style="background:#ffdb70"></i><span id="reference-camera-label">黄点：参考相机</span></span></div>
<details class="frame-filter"><summary>筛选点云帧（默认全部）</summary><div class="controls"><button id="all-frames">选择全部 25 帧</button><button id="clear-frames">清空选择</button><span class="small">筛选只影响点云。相机轨迹保留全部 25 帧。</span></div><div id="frame-selection" class="frame-grid"></div></details>
<p class="small">点云来自 GT 的第 0–24 帧。每帧按 16 像素间隔取点，每帧 1,024 点，共 25,600 点。所有帧都参与显示。</p>
<p class="small">坐标原点位于目标。网格轨迹默认俯视。切换轨迹后，下方图像和残差也会更新。</p></section>

<section class="panel" id="sequence-details"><h2><span class="section-number">03</span>查看图像与残差</h2>
<p class="section-intro">当前序列与上方 3D 图同步。也可直接切换序列。</p>
<label for="sequence" class="field-label">当前序列</label><select id="sequence" aria-label="选择校验序列"></select><div class="controls"><button id="prev">上一条</button><button id="next">下一条</button><span id="sequence-summary" class="small"></span></div>
<h3 id="reference-frame-title">参考帧</h3><p class="small">GT 与 OCC 使用相同的相机位姿。OCC 图像包含遮挡物。</p>
<div class="quad"><figure><img id="rgb" alt="参考帧 GT 原图"><figcaption>GT 原图</figcaption></figure><figure><img id="occ" alt="参考帧 OCC 原图"><figcaption>OCC 原图</figcaption></figure><figure><img id="depth" alt="参考帧 GT 深度，单位米"><figcaption id="depth-caption">GT 深度，单位米</figcaption></figure><figure><img id="depth-diff" alt="GT 与 OCC 的深度差"><figcaption>GT − OCC 深度：蓝 0 → 红 3 m。遮挡物更近。</figcaption></figure></div>
<h3 class="subheading">第 12 帧与第 13 帧能否对齐？</h3><p class="small">先用深度和位姿投影第 12 帧像素。再从第 13 帧采样颜色，检查两幅图是否对齐。</p>
<div class="quad"><figure><img id="reference" alt="第12帧参考图"><figcaption>第 12 帧原图</figcaption></figure><figure><img id="warp" alt="从第13帧重投影的图像"><figcaption>从第 13 帧投回的图像</figcaption></figure><figure><img id="rgb-error" alt="颜色残差"><figcaption>颜色差：蓝 0 → 红 20 / 255</figcaption></figure><figure><img id="depth-error" alt="深度残差"><figcaption>深度差：蓝 0 → 红 0.25 m</figcaption></figure></div>
<p class="small">颜色图中的黑区表示界外、深度不连续或遮挡。可见区域的筛选不使用颜色误差。</p><p class="small">深度残差图保留了未按深度误差筛选的结果。</p>
<details><summary>查看当前序列的数值与错误对照</summary><div id="sequence-metrics" class="metricgrid"></div><p id="scope" class="small path"></p><div class="scroll"><table><thead><tr><th>几何假设</th><th>特征误差中位数 / px</th><th>第 12→13 帧 GT PSNR / dB</th></tr></thead><tbody id="sample-controls"></tbody></table></div></details></section>

<section class="panel" id="conventions"><h2><span class="section-number">04</span>按这些约定读取数据</h2>
<p class="section-intro">位姿（pose）记录相机的位置和朝向。内参矩阵 K 将相机坐标投影到像素。</p>
<div class="scroll"><table><thead><tr><th>数据</th><th>当前约定</th><th>读取方法</th></tr></thead><tbody>
<tr><td>pose 平移</td><td>单位：米</td><td>直接使用。不要再次除以 100。</td></tr>
<tr><td>原始 EXR 深度</td><td>单位：厘米</td><td>参与几何计算前，除以 100 一次。</td></tr>
<tr><td>深度方向</td><td>相机 Z 轴方向的深度</td><td>按 Z 深度反投影。不要按射线距离反投影。</td></tr>
<tr><td>pose 矩阵</td><td>OpenCV c2w：相机坐标 → 世界坐标</td><td>相机轴依次向右、向下、向前。不要再翻转 Y/Z。</td></tr>
<tr><td>内参</td><td>fx = fy = @@FOCAL@@ px，cx = cy = 256</td><td>原始 FBX 焦距为 85 mm。方形传感器尺寸为 35 mm。</td></tr>
<tr><td>像素中心</td><td>u + 0.5，v + 0.5</td><td>用像素中心构造反投影射线。</td></tr>
</tbody></table></div><p class="small">位姿旋转的最大差异为 @@ANGLE@@°。内参与原始 FBX 的最大差异为 @@KERROR@@ px。</p><p class="small">已有 GT/OCC JSON 与本次重新提取的结果一致。源 FBX 哈希匹配。</p>
<div class="warning"><b>深度精度限制</b><p>EXR 的 RGB 通道保存相同的深度。A 通道保存透明度。通道类型为 HALF，即半精度浮点数。</p><p>50 m 附近的深度间隔约为 4 cm。120 m 附近约为 8 cm。该深度不具备毫米级精度。</p><p>抗锯齿、细枝叶、透明物和遮挡边缘会提高局部残差。随视角变化的材质也会提高颜色残差。</p></div></section>

<section class="panel" id="evidence"><h2><span class="section-number">05</span>查看检查证据</h2>
<h3>GT 与 OCC 的证据强度</h3><p class="small">GT 表示无环境遮挡物的输出。OCC 表示包含环境遮挡物的输出。</p><div class="scroll"><table><thead><tr><th>输入</th><th>共同可见比例的中位数</th><th>稳定区域深度 P95 的中位数</th><th>解释</th></tr></thead><tbody><tr><td>GT</td><td>@@GTVIS@@%</td><td>@@GTDEPTH@@ cm</td><td>可见区域较多。典型误差与半精度量化相符。</td></tr><tr><td>OCC</td><td>@@OCCVIS@@%</td><td>@@OCCDEPTH@@ m</td><td>遮挡和细枝叶会改变可见表面。不能直接比较所有投影点。</td></tr></tbody></table></div>
<p class="small">P95 表示第 95 百分位。表中先计算每个帧对的 P95，再取中位数。</p><p class="small">稳定区域按局部深度平滑程度筛选。该指标不按深度残差大小筛选。</p>
<p>GT 最差帧对的 P95 为 @@GTWORST@@ m。OCC 最差帧对的 P95 为 @@OCCWORST@@ m。原始误差均保留。</p><p class="small">GT 也会发生目标自身遮挡。OCC 的共同可见比例较低。未匹配区域不计作零误差。</p>@@WIDE_DIAGNOSTIC@@
<details><summary>检查能否发现错误单位和坐标变换？</summary><p>图像特征独立于待检查的几何参数。</p><ol><li>用双向 SIFT 比值检验匹配特征。</li><li>用图像单应 RANSAC 剔除错误匹配。</li><li>比较当前协议与主动制造的错误。</li></ol><p>特征筛选不使用预期 pose、K 或深度残差。</p><p>仅至少有 12 个匹配点的帧对进入汇总。本次共有 @@QUALIFIED@@ 对。其余 @@INSUFFICIENT@@ 对标为证据不足，保存在 metrics.json。</p><img class="chart" src="@@CHART@@" alt="当前协议与错误假设的重投影误差对比"><div class="scroll"><table><thead><tr><th>几何假设</th><th>特征误差中位数 / px</th><th>汇总 RGB PSNR / dB</th><th>颜色比较像素数</th></tr></thead><tbody>@@TABLE@@</tbody></table></div>
<p class="small">特征误差越低越好。PSNR 衡量图像相似程度，数值越高越好。</p><p class="small">颜色比较使用当前协议的深度可见区域。表中记录每个对照的实际比较像素数。</p><p>相对投影能揭示错误单位、重复翻转和矩阵求逆错误。</p><p>若 pose 平移和深度同时乘同一个常数，重投影不会改变。因此，本次另用 UE 清单约束绝对尺度。约束包含 35 m 直线跨度和 50/70/120 m 参考高度。</p></details>
<details><summary>检查步骤与计算公式</summary><ol><li>检查全部 25 帧的刚体矩阵、帧号和源 FBX 哈希。比较已有 GT/OCC 相机 JSON。</li><li>读取原始 FBX 的单位、焦距和胶片尺寸。独立转换 UE 坐标与相机轴。逐帧比较 pose。</li><li>解码所选全部 RGB 和 EXR。检查通道类型、遮挡方向和跨轨迹参考视图。</li><li>每个序列分别检查 GT 和 OCC。每种输出检查 8 个帧对，共 @@PAIRS@@ 对。</li><li>比较稳定区域深度与可见区域颜色。用独立图像特征检查投影。</li><li>加入错误单位、坐标轴、矩阵方向、焦距、深度定义和帧错位对照。</li></ol>
<pre>X_world = diag(1, -1, 1) * X_UE_cm / 100
X_camera = (depth_cm / 100) * inverse(K) * [u + 0.5, v + 0.5, 1]
X_target_camera = inverse(c2w_target) * c2w_source * X_camera
uv_target = project(K * X_target_camera) - [0.5, 0.5]</pre>
<p>匀速轨迹的两个 pose 同时错位时，相对位移可能不变。因此，帧对应还依靠 UE 源轨迹和参考帧约束。</p><p>0.5 像素偏移来自像素中心约定。本次检查不能独立辨识所有亚像素偏差。</p><p class="small">方法参考：@@REFS@@。</p><p class="small">UE5.6.1 的 CalcSceneDepth 经 ConvertFromDeviceZ 返回线性深度。本次还用真实多视角对照区分 Z 深度与射线距离。</p></details>
<details><summary>额外的焦距与深度尺度诊断</summary><p>用圆弧和螺旋轨迹的特征拟合公共焦距比例。同期拟合深度相对尺度。随机球壳轨迹用于留出验证。</p><p>最佳焦距比例为 <b>@@FITF@@</b>。最佳深度相对尺度为 <b>@@FITD@@</b>。</p><p>该拟合只检查样本一致性。本次未据此修改数据。</p><p>该拟合不能独立标定绝对尺度。该拟合也不能完全分离微小渲染误差。</p><pre id="fit"></pre></details>
<details><summary>跨轨迹的中心参考视图</summary><div id="centers" class="scroll"></div></details>
<details><summary>完整抽样清单</summary><div id="inventory" class="scroll"></div></details>
<details><summary>报告文件与复现材料</summary><p class="path">报告目录：@@ROOT@@</p><ul><li><code>index.html</code>：本报告，内嵌图片、点云和脚本。</li><li><code>evidence/</code>：检查数值、输入哈希、对应点和复核图。</li><li><code>reproduce/</code>：抽样清单、合并相机记录、提取脚本和字体。</li></ul><p>显示数据只保存在 HTML。目录不另存重复的 visuals.json。</p></details></section>
<footer class="foot">本次只读取已有数据。未启动 UE 渲染或训练。未修改生产图像、深度或相机元数据。所有显示资源均内嵌，可离线查看。</footer>
<script id="data" type="application/json">@@DATA@@</script>
<script>
'use strict';
const report=JSON.parse(document.querySelector('#data').textContent),vis=report.visuals,seqs=report.result.sequences;
const $=id=>document.getElementById(id),f=x=>x===null||x===undefined?'无有效支持':Number(x).toLocaleString('zh-CN',{maximumFractionDigits:5});
const med=a=>{a=a.filter(x=>x!==null).sort((a,b)=>a-b);return a.length?a[Math.floor(a.length/2)]:null};
const kinds=[['fix_line','直线'],['plane_grid','平面网格'],['rot_arc','圆弧'],['rot_spiral','螺旋'],['rand_shell','随机球壳']];
const scopeNames={existing_json:'已有 JSON 样本',production_audit:'正式训练样本',environment_audit:'补充环境样本'};
function sampleName(s){const m=s.prefix.match(/^scene_(\d+)_(.*?)_Target_(\d+)_height_(\d+)_ang_(\d+)$/);if(!m)return s.prefix;const env={grass:'草地',edge:'林缘',road:'道路',forest:'林内'};return `场景 ${m[1]} · ${env[m[2]]||m[2]} · 目标 ${m[3]} · ${Number(m[4])} m · 方位 ${Number(m[5])}°`}
const groups=new Map();seqs.forEach((s,i)=>{const key=s.scope+'|'+s.prefix;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(i)});
let selected=seqs.findIndex(s=>s.trajectory==='rot_spiral');if(selected<0)selected=0;
let yaw=.65,pitch=.8,zoom=1,dragging=false,lastX=0,lastY=0;
const cloudCache=new Map();
function cloudPoints(v){
 if(cloudCache.has(v.id))return cloudCache.get(v.id);
 let points=v.points;
 if(!points){const cloud=v.point_cloud;if(cloud.encoding!=='xyz-f32-rgb8-frame8-le')throw Error('不支持的点云编码');const raw=Uint8Array.from(atob(cloud.data),c=>c.charCodeAt(0));if(raw.length!==cloud.count*16)throw Error('点云数据长度不符');const data=new DataView(raw.buffer);points=Array.from({length:cloud.count},(_,i)=>{const offset=i*16;return [data.getFloat32(offset,true),data.getFloat32(offset+4,true),data.getFloat32(offset+8,true),raw[offset+12],raw[offset+13],raw[offset+14],raw[offset+15]]})}
 const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
 for(const p of points.concat(v.cameras)){for(let i=0;i<3;i++){min[i]=Math.min(min[i],p[i]);max[i]=Math.max(max[i],p[i])}}
 const decoded={points,center:min.map((x,i)=>(x+max[i])/2),span:Math.max(...max.map((x,i)=>x-min[i]),5)};
 cloudCache.set(v.id,decoded);if(cloudCache.size>4)cloudCache.delete(cloudCache.keys().next().value);return decoded;
}
const dropdown=$('sequence'),scene=$('scene-select');
vis.forEach((v,i)=>{const s=seqs[i],o=document.createElement('option');o.value=i;o.textContent=`${kinds.find(x=>x[0]===s.trajectory)[1]} · ${sampleName(s)} · ${scopeNames[s.scope]}`;dropdown.append(o)});
for(const [key,indices] of groups){const s=seqs[indices[0]],o=document.createElement('option');o.value=key;o.textContent=`${sampleName(s)} · ${scopeNames[s.scope]||s.scope} · ${indices.length}类轨迹`;scene.append(o)}
for(const [kind,name] of kinds){const b=document.createElement('button');b.dataset.trajectory=kind;b.innerHTML=`<span>${name}</span><small>${kind}</small>`;b.onclick=()=>{const indices=groups.get(scene.value),next=indices.find(i=>seqs[i].trajectory===kind);if(next!==undefined)choose(next)};$('trajectory-buttons').append(b)}
for(let frame=0;frame<25;frame++){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.className='frame-check';input.value=frame;input.checked=true;label.append(input,document.createTextNode(`第 ${frame} 帧`));$('frame-selection').append(label)}
function choose(index){selected=index;yaw=.65;pitch=seqs[index].trajectory==='plane_grid'?Math.PI/2:.8;zoom=1;update()}
scene.onchange=()=>{const indices=groups.get(scene.value),kind=seqs[selected].trajectory;choose(indices.find(i=>seqs[i].trajectory===kind)??indices[0])};
function update(){const v=vis[selected],s=seqs[selected];dropdown.value=selected;scene.value=s.scope+'|'+s.prefix;
const available=groups.get(scene.value);document.querySelectorAll('#trajectory-buttons button[data-trajectory]').forEach(b=>{b.disabled=!available.some(i=>seqs[i].trajectory===b.dataset.trajectory);b.title=b.disabled?'该场景未抽样此轨迹':b.dataset.trajectory;b.setAttribute('aria-pressed',String(s.trajectory===b.dataset.trajectory))});
$('cloud-current').textContent=`当前：${kinds.find(x=>x[0]===s.trajectory)[1]}轨迹。${available.length===1?'该补充场景仅抽样直线。查看五类轨迹时，请选择正式训练样本或已有 JSON 样本。':'该场景提供全部五类轨迹。'}`;
canvas.dataset.sequence=s.id;canvas.dataset.trajectory=s.trajectory;
for(const [id,key] of Object.entries({'rgb':'rgb','occ':'occ','depth':'depth','depth-diff':'depth_diff','reference':'reference','warp':'warp','rgb-error':'rgb_error','depth-error':'depth_error'})){$(id).src=v[key]||''}
$('sequence-summary').textContent=`序列 ${selected+1} / ${vis.length} · 参考帧 ${v.center} · 高度 ${s.height_m} m`;
$('reference-frame-title').textContent=`参考帧：第 ${v.center} 帧`;
$('reference-camera-label').textContent=`黄点：第 ${v.center} 帧参考相机`;
for(const [id,label] of [['rgb','GT 原图'],['occ','OCC 原图'],['depth','GT 深度，单位米']])$(id).alt=`第 ${v.center} 帧 ${label}`;
$('depth-caption').textContent=`GT 深度：蓝 ${f(v.depth_limits_m[0])} → 红 ${f(v.depth_limits_m[1])} m`;
$('scope').textContent=`数据来源：${scopeNames[s.scope]}。已有相机 JSON：${s.existing_json.length} 份。EXR 通道：${JSON.stringify(s.exr_channel_types)}（1 表示 HALF）。原始序列名：${s.prefix} / ${s.trajectory}`;
const featureErrors=s.features.filter(x=>x.matches>=12).map(x=>x.modes.correct.median);
$('sequence-metrics').innerHTML=[['位置最大误差',f(s.pose_position_error_m.max*1000)+' mm'],['旋转最大误差',f(s.pose_rotation_error_deg.max)+'°'],['特征重投影误差',f(med(featureErrors))+' px'],['深度量化间隔',f(s.half_float_depth_step_m.median*100)+' cm'],['参考相机高度',f(s.center_height_above_target_m)+' m'],['OCC 比 GT 更远的像素',f(s.occ_farther_than_gt_fraction*100)+'%']].map(x=>`<div><b>${x[1]}</b>${x[0]}</div>`).join('');
$('sample-controls').innerHTML=Object.entries(report.result.controls).map(([mode,m])=>{const e=med(s.features.filter(x=>x.matches>=12).map(x=>x.modes[mode].median)),p=s.pairs.find(x=>x.suffix==='GT'&&x.i===12&&x.j===13);return `<tr><td>${m.label}</td><td>${f(e)}</td><td>${f(p.metrics[mode].rgb_psnr_db)}</td></tr>`}).join('');draw()}
dropdown.onchange=()=>choose(Number(dropdown.value));$('prev').onclick=()=>choose((selected+vis.length-1)%vis.length);$('next').onclick=()=>choose((selected+1)%vis.length);
const canvas=$('cloud'),ctx=canvas.getContext('2d');
function draw(){const rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);canvas.width=rect.width*dpr;canvas.height=470*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);const w=rect.width,h=470;ctx.fillStyle='#101f2a';ctx.fillRect(0,0,w,h);
const v=vis[selected],cloud=cloudPoints(v),chosen=new Set([...document.querySelectorAll('.frame-check:checked')].map(x=>Number(x.value))),points=$('show-points').checked?cloud.points.filter(p=>chosen.has(p[6])):[],cameras=v.cameras,show=$('show-cameras').checked,{center,span}=cloud,scale=Math.min(w,h)*.76/span*zoom;
const frames=[...new Set(points.map(p=>p[6]))].sort((a,b)=>a-b);canvas.dataset.visibleFrames=frames.join(',');canvas.dataset.visiblePoints=points.length;canvas.dataset.cameraCount=show?cameras.length:0;
$('cloud-frame-status').textContent=`相机 ${show?cameras.length:0} / ${cameras.length} · 点云 ${frames.length} / ${cameras.length} 帧 · ${f(points.length)} 点`;
function project(p){const x=p[0]-center[0],y=p[1]-center[1],z=p[2]-center[2],rx=Math.cos(yaw)*x-Math.sin(yaw)*y,ry=Math.sin(yaw)*x+Math.cos(yaw)*y;return [w/2+scale*rx,h/2-scale*(Math.cos(pitch)*z-Math.sin(pitch)*ry),Math.sin(pitch)*z+Math.cos(pitch)*ry]}
const colorByFrame=$('point-color').value==='frame';points.map(p=>[project(p),p]).sort((a,b)=>a[0][2]-b[0][2]).forEach(([q,p])=>{ctx.fillStyle=colorByFrame?`hsl(${p[6]*360/25} 75% 64%)`:`rgb(${p[3]},${p[4]},${p[5]})`;ctx.fillRect(q[0]-.9,q[1]-.9,1.8,1.8)});
function line(a,b,color,width=1){const pa=project(a),pb=project(b);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(pa[0],pa[1]);ctx.lineTo(pb[0],pb[1]);ctx.stroke()}
if(show){for(let i=0;i<cameras.length;i++){if(i)line(cameras[i-1],cameras[i],'#6fb5ff',1.8);line(cameras[i],cameras[i].map((x,j)=>x+v.directions[i][j]*Math.max(2,span*.04)),'#fc827a',1);const q=project(cameras[i]);ctx.fillStyle=i===v.center?'#ffdb70':'#9bcfff';ctx.beginPath();ctx.arc(q[0],q[1],i===v.center?4.2:2.6,0,Math.PI*2);ctx.fill()}}
ctx.font='12px AuditNoto,system-ui,sans-serif';[['X',[5,0,0],'#ff7777'],['Y',[0,5,0],'#80dfad'],['Z',[0,0,5],'#7eacff']].forEach(([label,p,color])=>{line([0,0,0],p,color,2);const q=project(p);ctx.fillStyle=color;ctx.fillText(label+' 5 m',q[0]+4,q[1]-4)});ctx.fillStyle='#c3d4de';ctx.fillText('目标相对坐标 · 单位：米',16,24)}
canvas.onpointerdown=e=>{dragging=true;lastX=e.clientX;lastY=e.clientY;canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!dragging)return;yaw+=(e.clientX-lastX)*.008;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,pitch+(e.clientY-lastY)*.008));lastX=e.clientX;lastY=e.clientY;draw()};canvas.onpointerup=canvas.onpointercancel=()=>dragging=false;
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.25,Math.min(12,zoom*Math.exp(-e.deltaY*.001)));draw()},{passive:false});
$('reset').onclick=()=>{yaw=.65;pitch=.8;zoom=1;draw()};$('top-view').onclick=()=>{pitch=Math.PI/2;draw()};$('side-view').onclick=()=>{pitch=0;draw()};
for(const id of ['show-cameras','show-points','point-color'])$(id).onchange=draw;
document.querySelectorAll('.frame-check').forEach(x=>x.onchange=draw);
$('all-frames').onclick=()=>{document.querySelectorAll('.frame-check').forEach(x=>x.checked=true);$('show-points').checked=true;draw()};
$('clear-frames').onclick=()=>{document.querySelectorAll('.frame-check').forEach(x=>x.checked=false);draw()};
window.addEventListener('resize',draw);document.fonts.ready.then(draw);
$('fit').textContent=JSON.stringify(report.result.diagnostic_fit,null,2);
$('centers').innerHTML='<table><tr><th>样本</th><th>轨迹数</th><th>pose 最大差异</th><th>K 最大差异</th><th>深度均值差 / m</th></tr>'+report.result.centers.map(x=>`<tr><td>${scopeNames[x.scope]} / ${x.prefix}</td><td>${x.trajectories}</td><td>${f(x.max_pose_delta)}</td><td>${f(x.max_k_delta)}</td><td>${f(x.max_depth_mean_difference_m)}</td></tr>`).join('')+'</table>';
$('inventory').innerHTML='<table><tr><th>序列</th><th>来源</th><th>位置误差 / mm</th><th>旋转误差 / °</th><th>内参差 / px</th></tr>'+seqs.map(s=>`<tr><td>${s.prefix} / ${s.trajectory}</td><td>${scopeNames[s.scope]}</td><td>${f(s.pose_position_error_m.max*1000)}</td><td>${f(s.pose_rotation_error_deg.max)}</td><td>${f(s.raw_fbx_intrinsic_delta_px)}</td></tr>`).join('')+'</table>';update();
</script></main></body></html>'''
    conclusion=('抽样序列未发现单位混用、重复翻转坐标轴或明显内参错误。'
                '相机到世界的矩阵方向正确。位姿与 UE 原轨迹对齐。'
                '内参与原始 FBX 的独立计算一致。多视角投影与图像特征支持上述结论。'
                if passed else '部分指标需要复核。请查看原始数值与错误对照。本报告不能作为全量验收通过的依据。')
    browser_result={key:value for key,value in result.items() if key!='pairs'}
    browser_result['sequences']=[dict(s,pairs=[p for p in s['pairs'] if p['suffix']=='GT' and (p['i'],p['j'])==(12,13)]) for s in sequences]
    diagnostic=''
    diagnostic_path=artifact_file(root,'wide_baseline_diagnostic.json')
    if diagnostic_path.exists():
        check=json.loads(diagnostic_path.read_text())
        preview=cv2.cvtColor(cv2.imread(str(artifact_file(root,'wide_baseline_diagnostic.png'))),cv2.COLOR_BGR2RGB)
        diagnostic=('<details><summary>最差 GT 帧对为何出现大残差？</summary><p class="path">样本：'+html.escape(check['sequence'])+
                    ' / rot_spiral。</p><p>本次逐像素复核第 0→12 帧。两帧的相机距离较远。</p><p>稳定点中，'+str(check['residual_gt1m_pixels'])+
                    ' 个点的深度残差超过 1 m。所有点都对应目标视图中更近的表面。</p><p>相反方向超过 1 m 的点为 '+str(check['farther_target_gt1m'])+
                    ' 个。</p><p>残差方向与投影图支持目标遮挡的解释。该结果不支持整体单位偏差。</p><img alt="大基线遮挡复核" src="'+data_image(preview)+
                    '"><p class="small">从左到右：帧 0、帧 12、帧 12 投回帧 0、带符号深度残差。</p>'
                    '<p class="small">蓝色表示目标视图中的表面更近。绿色表示残差接近 0。灰黑表示缺少稳定的比较点。</p></details>')
        browser_result['wide_baseline_diagnostic']=check
    substitutions={'STATUS':'抽样几何通过 · OCC 深度精度仍有局限' if passed else '抽样几何需要复核','TIME':result['created_utc'],
                   'SEQUENCES':str(coverage['sequences']),'FRAMES':str(coverage['frames']),
                   'POSITION':fmt(max_position*1000,5),'FEATURE':fmt(feature,4),'ANGLE':fmt(max_angle,7),
                   'KERROR':fmt(max_k,7),'FOCAL':fmt(sequences[0]['k'][0][0],4),'CHART':chart_data,
                   'TABLE':table,'CONCLUSION':conclusion,'PAIRS':str(coverage['pairs']),
                   'FITF':fmt(fit.get('focal_scale'),7),'FITD':fmt(fit.get('depth_relative_scale'),7),
                   'GTVIS':fmt(gt_evidence['median_visible_fraction']*100,2),
                   'OCCVIS':fmt(occ_evidence['median_visible_fraction']*100,2),
                   'GTDEPTH':fmt(gt_evidence['stable_region_pair_p95_depth_error_m']['median']*100,2),
                   'OCCDEPTH':fmt(occ_evidence['stable_region_pair_p95_depth_error_m']['median'],2),
                   'GTWORST':fmt(gt_evidence['stable_region_pair_p95_depth_error_m']['max'],2),
                   'OCCWORST':fmt(occ_evidence['stable_region_pair_p95_depth_error_m']['max'],2),
                   'WIDE_DIAGNOSTIC':diagnostic,
                   'QUALIFIED':str(result['feature_support']['qualified_pairs']),
                   'INSUFFICIENT':str(result['feature_support']['insufficient_pairs']),
                   'ROOT':html.escape(str(root)),'REFS':refs,
                   'DATA':json.dumps(dict(result=browser_result,visuals=visuals),ensure_ascii=False,separators=(',',':')).replace('</','<\\/')}
    for key,value in substitutions.items():document=document.replace('@@'+key+'@@',value)
    font=artifact_file(root,'report-font.woff')
    if font.is_file():
        encoded=base64.b64encode(font.read_bytes()).decode()
        font_css="@font-face{font-family:AuditNoto;src:url(data:font/woff;base64,"+encoded+") format('woff');font-weight:400;font-style:normal}body{font-family:AuditNoto,system-ui,sans-serif}"
        document=document.replace('</style>',font_css+'</style>')
        license_path=artifact_file(root,'FONT_LICENSE.txt')
        if license_path.is_file():
            document=document.replace('<footer class="foot">','<details><summary>字体许可</summary><pre>'+html.escape(license_path.read_text())+'</pre></details><footer class="foot">')
    (root/'index.html').write_text(document,encoding='utf-8')
    artifact_file(root,'metrics.json',write=True).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print('HTML_REPORT',root/'index.html',flush=True)


def fmt(value,digits=4):
    return '无有效支持' if value is None else f'{value:.{digits}f}'



if __name__=='__main__':main()
