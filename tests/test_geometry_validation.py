"""Independent analytic checks for the empirical geometry audit, without UE."""
import importlib.util
import base64
import json
from pathlib import Path
import numpy as np

spec=importlib.util.spec_from_file_location('geometry_audit',Path(__file__).resolve().parents[1]/'scripts/forest_dataset/validate_geometry.py')
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_display_cloud_preserves_every_frame_and_metric_coordinates():
    poses=np.tile(np.eye(4),(25,1,1));poses[:,0,3]=np.arange(25)*.2
    payload=dict(frames=[dict(transform_matrix=p.tolist()) for p in poses],
                 intrinsics=[[100.,0,16],[0,100.,16],[0,0,1.]])
    depths=[np.full((32,32),5.+frame*.1) for frame in range(25)]
    colors=[np.full((32,32,3),frame,dtype=np.uint8) for frame in range(25)]
    target=np.array([1.,2.,3.])
    cloud=audit.point_cloud(payload,depths,colors,target)
    packed=np.frombuffer(base64.b64decode(cloud['data']),dtype=audit.POINT_DTYPE)
    assert cloud['frames']==list(range(25))
    assert cloud['counts_by_frame']==[4]*25
    assert cloud['count']==100
    assert audit.POINT_DTYPE.itemsize==16
    np.testing.assert_array_equal(packed['frame'],np.repeat(np.arange(25),4))
    for frame in range(25):
        z=5.+frame*.1
        expected=[(.5-16)/100*z+frame*.2-1,(.5-16)/100*z-2,z-3]
        np.testing.assert_allclose(packed['xyz'][frame*4],expected,atol=1e-6)
        np.testing.assert_array_equal(packed['rgb'][frame*4:frame*4+4],np.full((4,3),frame))


def test_display_data_can_be_recovered_after_duplicate_visuals_file_is_deleted(tmp_path):
    visuals=[{'id':'arc','cameras':[[0,0,5],[1,0,4]],'points':[[0,0,0,255,0,0,12]]}]
    payload=json.dumps({'visuals':visuals,'result':{}})
    (tmp_path/'index.html').write_text('<html><script id="data" type="application/json">'+payload+'</script></html>')
    assert audit.load_visuals(tmp_path)==visuals
    assert not (tmp_path/'visuals.json').exists()


def test_artifact_layout_reads_legacy_files_and_writes_into_named_directories(tmp_path):
    legacy=tmp_path/'metrics.json';legacy.write_text('{}')
    assert audit.artifact_file(tmp_path,'metrics.json')==legacy
    new=audit.artifact_file(tmp_path,'metrics.json',write=True)
    assert new==tmp_path/'evidence/metrics.json'
    new.write_text('{}');legacy.unlink()
    assert audit.artifact_file(tmp_path,'metrics.json')==new
    camera=audit.artifact_file(tmp_path,'cameras.json',write=True)
    assert camera==tmp_path/'reproduce/cameras.json'


def test_known_translation_projects_by_f_times_baseline_over_z():
    a=np.eye(4);b=np.eye(4);b[0,3]=.2
    k=np.array([[100.,0,32],[0,100,32],[0,0,1.]])
    q,z=audit.project_pixels(np.array([[31.5,31.5]]),np.array([5.]),a,b,k)
    np.testing.assert_allclose(q,[[27.5,31.5]],atol=1e-12)
    np.testing.assert_allclose(z,[5.])
    wrong,_=audit.project_pixels(np.array([[31.5,31.5]]),np.array([500.]),a,b,k)
    assert np.linalg.norm(wrong-q)>3.9


def test_camera_rotation_uses_inverse_c2w_rotation():
    a=np.eye(4);b=np.eye(4)
    b[:3,:3]=[[0,0,1],[0,1,0],[-1,0,0]]
    k=np.array([[100.,0,100],[0,100,100],[0,0,1.]])
    q,z=audit.project_pixels(np.array([[199.5,99.5]]),np.array([5.]),a,b,k)
    np.testing.assert_allclose(q,[[-.5,99.5]],atol=1e-10)
    np.testing.assert_allclose(z,[5.])


def test_common_global_scale_is_unobservable_in_reprojection():
    a=np.eye(4);b=np.eye(4);a[:3,3]=[2,3,4];b[:3,3]=[2.2,3.1,4]
    k=np.array([[100.,0,32],[0,100,32],[0,0,1.]])
    uv=np.array([[20.,30.],[40.,50.]])
    q,_=audit.project_pixels(uv,np.array([5.,6.]),a,b,k)
    aa=a.copy();bb=b.copy();aa[:3,3]*=100;bb[:3,3]*=100
    same,_=audit.project_pixels(uv,np.array([500.,600.]),aa,bb,k)
    np.testing.assert_allclose(same,q,atol=1e-10)


def test_full_resolution_sampling_exceeds_opencv_flattened_map_limit():
    yy,xx=np.mgrid[:512,:512]
    image=(xx+yy).astype(np.float32)
    sampled=audit.image_sample(image,np.c_[xx.ravel(),yy.ravel()])
    np.testing.assert_array_equal(sampled,image.ravel())


def plane():
    depth=np.full((64,64),5.,np.float32)
    yy,xx=np.mgrid[:64,:64]
    rgb=np.repeat(xx[:,:,None],3,axis=2).astype(np.uint8)
    target=np.repeat(np.minimum(xx+4,255)[:,:,None],3,axis=2).astype(np.uint8)
    poses=np.tile(np.eye(4),(25,1,1));poses[1,0,3]=.2
    k=np.array([[100.,0,32],[0,100,32],[0,0,1.]])
    return depth,rgb,target,poses,k


def test_dense_audit_detects_wrong_units_and_does_not_gate_on_rgb_error():
    d,rgb,target,poses,k=plane()
    good,mask=audit.dense_pair(d,d,rgb,target,poses,k,0,1,stride=1)
    bad,_=audit.dense_pair(d,d,rgb,target,poses,k,0,1,'depth_cm_as_m',stride=1,reference=mask['visible'])
    assert good['depth_error_all_m']['max']<1e-12
    assert good['rgb_psnr_db']>100
    assert bad['rgb_psnr_db']<45
    modified=np.full_like(target,255)
    _,changed=audit.dense_pair(d,d,rgb,modified,poses,k,0,1,stride=1)
    np.testing.assert_array_equal(changed['visible'],mask['visible'])


def test_behind_camera_has_no_support_or_invented_error_score():
    d,rgb,target,poses,k=plane()
    poses[1,:3,:3]=np.diag([-1,1,-1])
    metric,_=audit.dense_pair(d,d,rgb,target,poses,k,0,1,stride=1)
    assert metric['in_bounds']==0
    assert metric['rgb_psnr_db'] is None
    assert metric['depth_error_all_m']['count']==0


def test_feature_fit_can_recover_depth_scale_with_float32_input_depth():
    rng=np.random.default_rng(42)
    uv=rng.uniform(8,56,(80,2));true_depth=rng.uniform(4,7,80)
    k=np.array([[100.,0,32],[0,100,32],[0,0,1.]])
    true_k=k.copy();true_k[0,0]=true_k[1,1]=105
    rays=np.c_[(uv[:,0]+.5-32)/105,(uv[:,1]+.5-32)/105,np.ones(len(uv))]
    world=rays*true_depth[:,None]
    items=[]
    for kind,angle in [('rot_arc',.12),('rot_spiral',-.16),('rand_shell',.08)]:
        b=np.eye(4);b[:3,3]=[.4,.1,0]
        c,s=np.cos(angle),np.sin(angle)
        b[:3,:3]=[[c,0,s],[0,1,0],[-s,0,c]]
        camera=(world-b[:3,3])@b[:3,:3]
        projected=np.c_[105*camera[:,0]/camera[:,2]+31.5,105*camera[:,1]/camera[:,2]+31.5]
        items.append(dict(kind=kind,ua=uv,ub=projected,depth=(true_depth*.97).astype(np.float32),
                          a=np.eye(4),b=b,k=k))
    fitted=audit.fit_feature_geometry(items)
    assert abs(fitted['focal_scale']-1.05)<1e-4
    assert abs(fitted['depth_relative_scale']-1/.97)<1e-4
