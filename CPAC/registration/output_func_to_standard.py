import nipype.interfaces.fsl as fsl
import nipype.pipeline.engine as pe
import nipype.interfaces.utility as util
import nipype.interfaces.ants as ants
import nipype.interfaces.c3 as c3
from CPAC.registration.utils import change_itk_transform_type

# Todo: CC distcor is not implement for fsl apply xform func to mni, why ??
def fsl_apply_transform_func_to_mni(
        workflow,
        output_name, 
        func_key, 
        ref_key,
        num_strat, 
        strat, 
        interpolation_method, 
        distcor=False, 
        map_node=False
    ):

    strat_nodes = strat.get_nodes_names()

    # if the input is a string, assume that it is resource pool key, 
    # if it is a tuple, assume that it is a node, outfile pair,
    # otherwise, something funky is going on
    if isinstance(func_key, str):
        if func_key == "leaf":
            func_node, func_file = strat.get_leaf_properties()
        else:
            func_node, func_file = strat[func_key]
    elif isinstance(func_key, tuple):
        func_node, func_file = func_key

    if isinstance(ref_key, str):
        ref_node, ref_out_file = strat[ref_key]
    elif isinstance(ref_key, tuple):
        ref_node, ref_out_file = ref_key

    if map_node == True:
        # func_mni_warp
        func_mni_warp = pe.MapNode(interface=fsl.ApplyWarp(),
                name='func_mni_fsl_warp_{0}_{1:d}'.format(output_name, num_strat),
                iterfield=['in_file'],
                mem_gb=1.5)
    else:
        # func_mni_warp
        func_mni_warp = pe.Node(interface=fsl.ApplyWarp(),
                name='func_mni_fsl_warp_{0}_{1:d}'.format(output_name, num_strat))

        
    func_mni_warp.inputs.interp = interpolation_method

    workflow.connect(func_node, func_file,
                     func_mni_warp, 'in_file')

    workflow.connect(ref_node, ref_out_file,
                     func_mni_warp, 'ref_file')

    if 'anat_mni_fnirt_register' in strat_nodes:

        node, out_file = strat['functional_to_anat_linear_xfm']
        workflow.connect(node, out_file,
                         func_mni_warp, 'premat')

        node, out_file = strat['anatomical_to_mni_nonlinear_xfm']
        workflow.connect(node, out_file,
                         func_mni_warp, 'field_file')

    elif 'anat_mni_flirt_register' in strat_nodes:

        if 'functional_to_mni_linear_xfm' not in strat:

            combine_transforms = pe.Node(interface=fsl.ConvertXFM(),
                name='combine_fsl_xforms_{0}_{1:d}'.format(output_name,\
                        num_strat))

            combine_transforms.inputs.concat_xfm = True

            node, out_file = strat['anatomical_to_mni_linear_xfm']
            workflow.connect(node, out_file,
                             combine_transforms, 'in_file2')

            node, out_file = strat['functional_to_anat_linear_xfm']
            workflow.connect(node, out_file,
                             combine_transforms, 'in_file')

            strat.update_resource_pool({ 'functional_to_mni_linear_xfm':
                (combine_transforms, 'out_file')}) 
            strat.append_name(combine_transforms.name)

        combine_transforms, outfile = strat['functional_to_mni_linear_xfm']

        workflow.connect(combine_transforms, outfile,
                         func_mni_warp, 'premat')
        
    else:
        raise ValueError(
                'Could not find flirt or fnirt registration in nodes')
        
    strat.update_resource_pool({ output_name: (func_mni_warp, 'out_file')}) 
    strat.append_name(func_mni_warp.name)

    return workflow


def ants_apply_warps_func_mni(
        workflow,
        output_name,
        func_key,
        ref_key,
        num_strat,
        strat,
        interpolation_method='LanczosWindowedSinc',
        distcor=False,
        map_node=False,
        inverse=False,
        symmetry='asymmetric',
        input_image_type=0,
        num_ants_cores=1
    ):

    """
    Applies previously calculated ANTS registration transforms to input
    images. This workflow employs the antsApplyTransforms tool:

    http://stnava.github.io/ANTs/

    Parameters
    ----------
    name : string, optional
        Name of the workflow.

    Returns
    -------
    apply_ants_warp_wf : nipype.pipeline.engine.Workflow

    Notes
    -----

    Workflow Inputs::

        workflow: Nipype workflow object
            the workflow containing the resources involved
        output_name: str
            what the name of the warped functional should be when written to the
            resource pool
        func_key: string
            resource pool key correspoding to the node containing the 3D or 4D
            functional file to be written into MNI space, use 'leaf' for a 
            leaf node
        ref_key: string
            resource pool key correspoding to the file path to the template brain
            used for functional-to-template registration
        num_strat: int
            the number of strategy objects
        strat: C-PAC Strategy object
            a strategy with one or more resource pools
        interpolation_method: str
            which interpolation to use when applying the warps, commonly used
            options are 'Linear', 'Bspline', 'LanczosWindowedSinc' (default) 
            for derivatives and image data 'NearestNeighbor' for masks
        distcor: boolean
            indicates whether a distortion correction transformation should be 
            added to the transforms, this of course requires that a distortion
            correction map exist in the resource pool
        map_node: boolean
            indicates whether a mapnode should be used, if TRUE func_key is 
            expected to correspond to a list of resources that should each 
            be written into standard space with the other parameters
        inverse: boolean
            writes the invrse of the transform, i.e. MNI->EPI instead of
            EPI->MNI
        input_image_type: int
            argument taken by the ANTs apply warp tool; in this case, should be
            0 for scalars (default) and 3 for 4D functional time-series
        num_ants_cores: int
            the number of CPU cores dedicated to ANTS anatomical-to-standard
            registration
            
    Workflow Outputs::
    
        outputspec.output_image : string (nifti file)
            Normalized output file

                 
    Workflow Graph:
    
    .. image::
        :width: 500
    
    Detailed Workflow Graph:
    
    .. image:: 
        :width: 500

    Apply the functional-to-structural and structural-to-template warps to
    the 4D functional time-series to warp it to template space.

    Parameters
    ----------
    """

    # if the input is a string, assume that it is resource pool key, 
    # if it is a tuple, assume that it is a node, outfile pair,
    # otherwise, something funky is going on
    if isinstance(func_key, str):
        if func_key == "leaf":
            input_node, input_out = strat.get_leaf_properties()
        else:
            input_node, input_out = strat[func_key]
    elif isinstance(func_key, tuple):
        input_node, input_out = func_key

    if isinstance(ref_key, str):
        ref_node, ref_out = strat[ref_key]
    elif isinstance(ref_key, tuple):
        ref_node, ref_out = func_key


    # when inverse is enabled, we want to update the name of various
    # nodes so that we know they were inverted
    inverse_string = ''
    if inverse is True:
        inverse_string = '_inverse'

    # make sure that resource pool has some required resources before proceeding
    if 'fsl_mat_as_itk' not in strat:

        fsl_reg_2_itk = pe.Node(c3.C3dAffineTool(),
                name='fsl_reg_2_itk_{0}'.format(num_strat))
        fsl_reg_2_itk.inputs.itk_transform = True
        fsl_reg_2_itk.inputs.fsl2ras = True

        # convert the .mat from linear Func->Anat to
        # ANTS format
        node, out_file = strat['functional_to_anat_linear_xfm']
        workflow.connect(node, out_file, fsl_reg_2_itk, 'transform_file')

        node, out_file = strat['anatomical_brain']
        workflow.connect(node, out_file, fsl_reg_2_itk, 'reference_file')

        ref_node, ref_out = strat['mean_functional']
        workflow.connect(ref_node, ref_out, fsl_reg_2_itk, 'source_file')

        itk_imports = ['import os']
        change_transform = pe.Node(util.Function(
                input_names=['input_affine_file'],
                output_names=['updated_affine_file'],
                function=change_itk_transform_type,
                imports=itk_imports),
                name='change_transform_type_{0}'.format(num_strat))

        workflow.connect(fsl_reg_2_itk, 'itk_transform',
                change_transform, 'input_affine_file')

        strat.update_resource_pool({
            'fsl_mat_as_itk': (change_transform, 'updated_affine_file')
        })

        strat.append_name(fsl_reg_2_itk.name)

    # stack of transforms to be combined to acheive the desired transformation
    num_transforms = 5
    collect_transforms_key = \
            'collect_transforms{0}'.format(inverse_string)

    if distcor is True:
        num_transforms = 6
        collect_transforms_key = \
                'collect_transforms{0}{1}'.format('_distcor',
                        inverse_string)

    if collect_transforms_key not in strat:

        # handle both symmetric and asymmetric transforms
        ants_transformation_dict =  {
                'asymmetric': {
                    'anatomical_to_mni_nonlinear_xfm': 'anatomical_to_mni_nonlinear_xfm',
                    'ants_affine_xfm': 'ants_affine_xfm',
                    'ants_rigid_xfm': 'ants_rigid_xfm',
                    'ants_initial_xfm': 'ants_initial_xfm',
                    'blip_warp': 'blip_warp',
                    'blip_warp_inverse': 'blip_warp_inverse',
                    'fsl_mat_as_itk': 'fsl_mat_as_itk',
                    },
                 'symmetric': {
                    'anatomical_to_mni_nonlinear_xfm': 'ants_symm_warp_field',
                    'ants_affine_xfm': 'ants_symm_affine_xfm',
                    'ants_rigid_xfm': 'ants_symm_rigid_xfm',
                    'ants_initial_xfm': 'ants_symm_initial_xfm',
                    'blip_warp': 'blip_warp',
                    'blip_warp_inverse': 'blip_warp_inverse',
                    'fsl_mat_as_itk': 'fsl_mat_as_itk',
                     }
                }

        # transforms to be concatenated, the first element of each tuple is
        # the resource pool key related to the resource that should be 
        # connected in, and the second element is the input to which it 
        # should be connected
        if inverse is True:
            if distcor is True:
                # Field file from anatomical nonlinear registration
                transforms_to_combine = [\
                        ('anatomical_to_mni_nonlinear_xfm', 'in6'),
                        ('ants_affine_xfm', 'in5'),
                        ('ants_rigid_xfm', 'in4'),
                        ('ants_initial_xfm', 'in3'),
                        ('fsl_mat_as_itk', 'in2'),
                        ('blip_warp_inverse', 'in1')]
            else:
                transforms_to_combine = [\
                        ('anatomical_to_mni_nonlinear_xfm', 'in5'),
                        ('ants_affine_xfm', 'in4'),
                        ('ants_rigid_xfm', 'in3'),
                        ('ants_initial_xfm', 'in2'),
                        ('fsl_mat_as_itk', 'in1')]
        else:
            transforms_to_combine = [\
                    ('anatomical_to_mni_nonlinear_xfm', 'in1'),
                    ('ants_affine_xfm', 'in2'),
                    ('ants_rigid_xfm', 'in3'),
                    ('ants_initial_xfm', 'in4'),
                    ('fsl_mat_as_itk', 'in5')]

            if distcor is True:
                transforms_to_combine.append(('blip_warp', 'in6'))

        # define the node
        collect_transforms = pe.Node(util.Merge(num_transforms),
                name='collect_transforms{0}_{1}'.format(inverse_string, num_strat))

        # wire in the various tranformations
        for transform_key, input_port in transforms_to_combine:
             node, out_file = strat[ants_transformation_dict[symmetry][transform_key]]
             workflow.connect(node, out_file,
                 collect_transforms, input_port)

        # set the output
        strat.update_resource_pool({
            collect_transforms_key: (collect_transforms, 'out')
        })

        strat.append_name(collect_transforms.name)

    #### now we add in the apply ants warps node
    if map_node:
        apply_ants_warp = pe.MapNode(
                interface=ants.ApplyTransforms(),
                name='apply_ants_warp_{0}_mapnode{1}_{2}'.format(output_name,
                    inverse_string, num_strat),
                iterfield=['input_image'], mem_gb=1.5)
    else:
        apply_ants_warp = pe.Node(
                interface=ants.ApplyTransforms(),
                name='apply_ants_warp_{0}{1}_{2}'.format(output_name,
                    inverse_string, num_strat), mem_gb=1.5)

    apply_ants_warp.inputs.out_postfix = '_antswarp'
    apply_ants_warp.interface.num_threads = int(num_ants_cores)

    if inverse is True:
        apply_ants_warp.inputs.invert_transform_flags = \
                [True, True, True, True, False]

    # input_image_type:
    # (0 or 1 or 2 or 3)
    # Option specifying the input image type of scalar
    # (default), vector, tensor, or time series.
    apply_ants_warp.inputs.input_image_type = input_image_type
    apply_ants_warp.inputs.dimension = 3
    apply_ants_warp.inputs.interpolation = interpolation_method

    node, out_file = strat[ref_key]
    workflow.connect(node, out_file,
            apply_ants_warp, 'reference_image')

    collect_node, collect_out = strat[collect_transforms_key]
    workflow.connect(collect_node, collect_out,
                     apply_ants_warp, 'transforms')

    workflow.connect(input_node, input_out,
                     apply_ants_warp, 'input_image')

    strat.update_resource_pool({
        output_name: (apply_ants_warp, 'output_image')
    })

    strat.append_name(apply_ants_warp.name)

    return workflow

def output_func_to_standard(workflow, func_key, ref_key, output_name,
        strat, num_strat, pipeline_config_obj, input_image_type='func_derivative',
        symmetry='asymmetric', inverse=False):

    image_types = ['func_derivative', 'func_derivative_multi',
            'func_4d', 'func_mask']

    if input_image_type not in image_types:
        raise ValueError('Input image type {0} should be one of {1}'.format(\
                input_image_type, ', '.join(image_types)))

    nodes = strat.get_nodes_names()
    
    map_node = True if input_image_type == 'func_derivative_multi' else False

    distcor = True if 'epi_distcorr' in nodes or \
            'blip_correct' in nodes else False

    if 'anat_mni_fnirt_register' in nodes or \
        'anat_mni_flirt_register' in nodes:
        # 'func_to_epi_fsl' in nodes:

        if input_image_type == 'map' or 'mask' in input_image_type:
            interp = 'nn'
        else:
            interp = pipeline_config_obj.funcRegFSLinterpolation

        fsl_apply_transform_func_to_mni(workflow, output_name, func_key,
                ref_key, num_strat, strat, interp, distcor=distcor,
                map_node=map_node)

    elif 'ANTS' in pipeline_config_obj.regOption:

        if input_image_type == 'map' or 'mask' in input_image_type:
            interp = 'NearestNeighbor'
        else:
            interp = pipeline_config_obj.funcRegANTSinterpolation

        image_type = 3 if input_image_type == 'func_4d' else 0

        ants_apply_warps_func_mni(workflow, output_name, func_key, ref_key,
                num_strat, strat, interpolation_method=interp,
                distcor=distcor, map_node=map_node, inverse=inverse,
                symmetry=symmetry, input_image_type=image_type,
                num_ants_cores=pipeline_config_obj.num_ants_threads)

    else:
        raise ValueError('Cannot determine whether a ANTS or FSL registration' \
                'is desired, check your pipeline.')

    return workflow
