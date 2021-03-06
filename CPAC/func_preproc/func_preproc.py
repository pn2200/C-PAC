from nipype import logging
from nipype.interfaces import ants

logger = logging.getLogger('workflow')

import nipype.pipeline.engine as pe
import nipype.interfaces.fsl as fsl
import nipype.interfaces.utility as util
from nipype.interfaces import afni
from nipype.interfaces.afni import preprocess
from nipype.interfaces.afni import utils as afni_utils

from CPAC.func_preproc.utils import add_afni_prefix, nullify


def collect_arguments(*args):
    command_args = []
    if args[0]:
        command_args += [args[1]]
    command_args += args[2:]
    return ' '.join(command_args)


def skullstrip_functional(skullstrip_tool='afni', anatomical_mask_dilation=False, wf_name='skullstrip_functional'):

    skullstrip_tool = skullstrip_tool.lower()
    if skullstrip_tool != 'afni' and skullstrip_tool != 'fsl' and skullstrip_tool != 'fsl_afni' and skullstrip_tool != 'anatomical_refined':
        raise Exception("\n\n[!] Error: The 'tool' parameter of the "
                        "'skullstrip_functional' workflow must be either "
                        "'afni' or 'fsl' or 'fsl_afni' or 'anatomical_refined'.\n\nTool input: "
                        "{0}\n\n".format(skullstrip_tool))
               
    wf = pe.Workflow(name=wf_name)

    input_node = pe.Node(util.IdentityInterface(fields=['func',
                                                        'anatomical_brain_mask',
                                                        'anat_skull']),
                         name='inputspec')

    output_node = pe.Node(util.IdentityInterface(fields=['func_brain',
                                                         'func_brain_mask']),
                         name='outputspec')

    if skullstrip_tool == 'afni':
        func_get_brain_mask = pe.Node(interface=preprocess.Automask(),
                                      name='func_get_brain_mask_AFNI')
        func_get_brain_mask.inputs.outputtype = 'NIFTI_GZ'

        wf.connect(input_node, 'func', func_get_brain_mask, 'in_file')

        wf.connect(func_get_brain_mask, 'out_file',
                   output_node, 'func_brain_mask')

    elif skullstrip_tool == 'fsl':
        func_get_brain_mask = pe.Node(interface=fsl.BET(),
                                      name='func_get_brain_mask_BET')

        func_get_brain_mask.inputs.mask = True
        func_get_brain_mask.inputs.functional = True

        erode_one_voxel = pe.Node(interface=fsl.ErodeImage(),
                                  name='erode_one_voxel')

        erode_one_voxel.inputs.kernel_shape = 'box'
        erode_one_voxel.inputs.kernel_size = 1.0

        wf.connect(input_node, 'func', func_get_brain_mask, 'in_file')

        wf.connect(func_get_brain_mask, 'mask_file',
                   erode_one_voxel, 'in_file')

        wf.connect(erode_one_voxel, 'out_file',
                   output_node, 'func_brain_mask')

    elif skullstrip_tool == 'fsl_afni':
        skullstrip_first_pass = pe.Node(fsl.BET(frac=0.2, mask=True, functional=True), name='skullstrip_first_pass')
        bet_dilate = pe.Node(fsl.DilateImage(operation='max', kernel_shape='sphere', kernel_size=6.0, internal_datatype='char'), name='skullstrip_first_dilate')                                                  
        bet_mask = pe.Node(fsl.ApplyMask(), name='skullstrip_first_mask')
        unifize = pe.Node(afni_utils.Unifize(t2=True, outputtype='NIFTI_GZ', args='-clfrac 0.2 -rbt 18.3 65.0 90.0', out_file="uni.nii.gz"), name='unifize')
        skullstrip_second_pass = pe.Node(preprocess.Automask(dilate=1, outputtype='NIFTI_GZ'), name='skullstrip_second_pass')
        combine_masks = pe.Node(fsl.BinaryMaths(operation='mul'), name='combine_masks')

        wf.connect([(input_node, skullstrip_first_pass, [('func', 'in_file')]),
                        (skullstrip_first_pass, bet_dilate, [('mask_file', 'in_file')]),
                        (bet_dilate, bet_mask, [('out_file', 'mask_file')]),
                        (skullstrip_first_pass, bet_mask, [('out_file' , 'in_file')]),
                        (bet_mask, unifize, [('out_file', 'in_file')]),
                        (unifize, skullstrip_second_pass, [('out_file', 'in_file')]),
                        (skullstrip_first_pass, combine_masks, [('mask_file', 'in_file')]),
                        (skullstrip_second_pass, combine_masks, [('out_file', 'operand_file')]),
                        (combine_masks, output_node, [('out_file', 'func_brain_mask')])])
    
    # Refine functional mask by registering anatomical mask to functional space
    elif skullstrip_tool == 'anatomical_refined':

        # Get functional mean to use later as reference, when transform anatomical mask to functional space
        func_skull_mean = pe.Node(interface=afni_utils.TStat(),
                                    name='func_skull_mean')
        func_skull_mean.inputs.options = '-mean'
        func_skull_mean.inputs.outputtype = 'NIFTI_GZ'

        wf.connect(input_node, 'func', func_skull_mean, 'in_file')


        # Register func to anat
        linear_reg_func_to_anat = pe.Node(interface=fsl.FLIRT(),
                         name='linear_reg_func_to_anat')
        linear_reg_func_to_anat.inputs.cost = 'mutualinfo'
        linear_reg_func_to_anat.inputs.dof = 6
        
        wf.connect(func_skull_mean, 'out_file', linear_reg_func_to_anat, 'in_file')
        wf.connect(input_node, 'anat_skull', linear_reg_func_to_anat, 'reference')


        # Inverse func to anat affine
        inv_func_to_anat_affine = pe.Node(interface=fsl.ConvertXFM(),
                                name='inv_func_to_anat_affine')
        inv_func_to_anat_affine.inputs.invert_xfm = True

        wf.connect(linear_reg_func_to_anat, 'out_matrix_file',
                                    inv_func_to_anat_affine, 'in_file')


        # Transform anatomical mask to functional space
        linear_trans_mask_anat_to_func = pe.Node(interface=fsl.FLIRT(),
                         name='linear_trans_mask_anat_to_func')
        linear_trans_mask_anat_to_func.inputs.apply_xfm = True
        linear_trans_mask_anat_to_func.inputs.cost = 'mutualinfo'
        linear_trans_mask_anat_to_func.inputs.dof = 6
        linear_trans_mask_anat_to_func.inputs.interp = 'nearestneighbour'


        # Dialate anatomical mask, if 'anatomical_mask_dilation : True' in config file
        if anatomical_mask_dilation :
            anat_mask_dilate = pe.Node(interface=afni.MaskTool(),
                            name='anat_mask_dilate')
            anat_mask_dilate.inputs.dilate_inputs = '1'
            anat_mask_dilate.inputs.outputtype = 'NIFTI_GZ'

            wf.connect(input_node, 'anatomical_brain_mask', anat_mask_dilate, 'in_file' )
            wf.connect(anat_mask_dilate, 'out_file', linear_trans_mask_anat_to_func, 'in_file')

        else: 
            wf.connect(input_node, 'anatomical_brain_mask', linear_trans_mask_anat_to_func, 'in_file')

        wf.connect(func_skull_mean, 'out_file', linear_trans_mask_anat_to_func, 'reference')
        wf.connect(inv_func_to_anat_affine, 'out_file',
                                    linear_trans_mask_anat_to_func, 'in_matrix_file')
        wf.connect(linear_trans_mask_anat_to_func, 'out_file',
                                    output_node, 'func_brain_mask')

    func_edge_detect = pe.Node(interface=afni_utils.Calc(),
                               name='func_extract_brain')

    func_edge_detect.inputs.expr = 'a*b'
    func_edge_detect.inputs.outputtype = 'NIFTI_GZ'

    wf.connect(input_node, 'func', func_edge_detect, 'in_file_a')

    if skullstrip_tool == 'afni':
        wf.connect(func_get_brain_mask, 'out_file',
                   func_edge_detect, 'in_file_b')
    elif skullstrip_tool == 'fsl':
        wf.connect(erode_one_voxel, 'out_file',
                   func_edge_detect, 'in_file_b')
    elif skullstrip_tool == 'fsl_afni':
        wf.connect(combine_masks, 'out_file',
                        func_edge_detect, 'in_file_b')
    elif skullstrip_tool == 'anatomical_refined':
        wf.connect(linear_trans_mask_anat_to_func, 'out_file',
                        func_edge_detect, 'in_file_b')

    wf.connect(func_edge_detect, 'out_file',  output_node, 'func_brain')

    return wf


def create_scale_func_wf(runScaling, scaling_factor, wf_name='scale_func'):

    """Workflow to scale func data.
    Parameters
    ----------
        runScaling : boolean
            Whether scale func data or not. Usually scaling used in rodent raw data.
        scaling_factor : float
            Scale the size of the dataset voxels by the factor. 
        wf_name : string
            name of the workflow
    
    Workflow Inputs::
        inputspec.func : func file or a list of func/rest nifti file
            User input functional(T2*) Image
    Workflow Outputs::
        outputspec.scaled_func : string (nifti file)
            Path to Output image with scaled data
    Order of commands:
    - Scale the size of the dataset voxels by the factor 'fac'. For details see `3dcalc <https://afni.nimh.nih.gov/pub/dist/doc/program_help/3drefit.html>`_::
        3drefit -xyzscale fac rest.nii.gz
    """

    # allocate a workflow object
    preproc = pe.Workflow(name=wf_name)

    # configure the workflow's input spec
    inputNode = pe.Node(util.IdentityInterface(fields=['func']),
                        name='inputspec')

    # configure the workflow's output spec
    outputNode = pe.Node(util.IdentityInterface(fields=['scaled_func']),
                         name='outputspec')

    if runScaling == True:

        # allocate a node to edit the functional file
        func_scale = pe.Node(interface=afni_utils.Refit(),
                                name='func_scale')

        func_scale.inputs.xyzscale = scaling_factor

        # wire in the func_get_idx node
        preproc.connect(inputNode, 'func',
                        func_scale, 'in_file')

        # wire the output
        preproc.connect(func_scale, 'out_file',
                        outputNode, 'scaled_func')

    else:
        preproc.connect(inputNode, 'func',
                        outputNode, 'scaled_func')

    return preproc

    
def create_wf_edit_func(wf_name="edit_func"):
    """Workflow to edit the scan to the proscribed TRs.
    
    Workflow Inputs::

        inputspec.func : func file or a list of func/rest nifti file
            User input functional(T2*) Image

        inputspec.start_idx : string
            Starting volume/slice of the functional image (optional)

        inputspec.stop_idx : string
            Last volume/slice of the functional image (optional)

    Workflow Outputs::

        outputspec.edited_func : string (nifti file)
            Path to Output image with the initial few slices dropped


    Order of commands:

    - Get the start and the end volume index of the functional run. If not defined by the user, return the first and last volume.

        get_idx(in_files, stop_idx, start_idx)

    - Dropping the initial TRs. For details see `3dcalc <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dcalc.html>`_::

        3dcalc -a rest.nii.gz[4..299]
               -expr 'a'
               -prefix rest_3dc.nii.gz

    """

    # allocate a workflow object
    preproc = pe.Workflow(name=wf_name)

    # configure the workflow's input spec
    inputNode = pe.Node(util.IdentityInterface(fields=['func',
                                                       'start_idx',
                                                       'stop_idx']),
                        name='inputspec')

    # configure the workflow's output spec
    outputNode = pe.Node(util.IdentityInterface(fields=['edited_func']),
                         name='outputspec')

    # allocate a node to check that the requested edits are
    # reasonable given the data
    func_get_idx = pe.Node(util.Function(input_names=['in_files',
                                                      'stop_idx',
                                                      'start_idx'],
                                         output_names=['stopidx',
                                                       'startidx'],
                                         function=get_idx),
                           name='func_get_idx')

    # wire in the func_get_idx node
    preproc.connect(inputNode, 'func',
                    func_get_idx, 'in_files')
    preproc.connect(inputNode, 'start_idx',
                    func_get_idx, 'start_idx')
    preproc.connect(inputNode, 'stop_idx',
                    func_get_idx, 'stop_idx')

    # allocate a node to edit the functional file
    func_drop_trs = pe.Node(interface=afni_utils.Calc(),
                            name='func_drop_trs')

    func_drop_trs.inputs.expr = 'a'
    func_drop_trs.inputs.outputtype = 'NIFTI_GZ'

    # wire in the inputs
    preproc.connect(inputNode, 'func',
                    func_drop_trs, 'in_file_a')

    preproc.connect(func_get_idx, 'startidx',
                    func_drop_trs, 'start_idx')

    preproc.connect(func_get_idx, 'stopidx',
                    func_drop_trs, 'stop_idx')

    # wire the output
    preproc.connect(func_drop_trs, 'out_file',
                    outputNode, 'edited_func')

    return preproc


# functional preprocessing
def create_func_preproc(skullstrip_tool, n4_correction, anatomical_mask_dilation=False, wf_name='func_preproc'):
    """

    The main purpose of this workflow is to process functional data. Raw rest file is deobliqued and reoriented
    into RPI. Then take the mean intensity values over all time points for each voxel and use this image
    to calculate motion parameters. The image is then skullstripped, normalized and a processed mask is
    obtained to use it further in Image analysis.

    Parameters
    ----------

    wf_name : string
        Workflow name

    Returns
    -------
    func_preproc : workflow object
        Functional Preprocessing workflow object

    Notes
    -----

    `Source <https://github.com/FCP-INDI/C-PAC/blob/master/CPAC/func_preproc/func_preproc.py>`_

    Workflow Inputs::

        inputspec.func : func nifti file
            User input functional(T2) Image, in any of the 8 orientations

        inputspec.twopass : boolean
            Perform two-pass on volume registration

    Workflow Outputs::

        outputspec.refit : string (nifti file)
            Path to deobliqued anatomical data

        outputspec.reorient : string (nifti file)
            Path to RPI oriented anatomical data

        outputspec.motion_correct_ref : string (nifti file)
             Path to Mean intensity Motion corrected image
             (base reference image for the second motion correction run)

        outputspec.motion_correct : string (nifti file)
            Path to motion corrected output file

        outputspec.max_displacement : string (Mat file)
            Path to maximum displacement (in mm) for brain voxels in each volume

        outputspec.movement_parameters : string (Mat file)
            Path to 1D file containing six movement/motion parameters(3 Translation, 3 Rotations)
            in different columns (roll pitch yaw dS  dL  dP)

        outputspec.skullstrip : string (nifti file)
            Path to skull stripped Motion Corrected Image

        outputspec.mask : string (nifti file)
            Path to brain-only mask

        outputspec.func_mean : string (nifti file)
            Mean, Skull Stripped, Motion Corrected output T2 Image path
            (Image with mean intensity values across voxels)

        outputpsec.preprocessed : string (nifti file)
            output skull stripped, motion corrected T2 image
            with normalized intensity values

        outputspec.preprocessed_mask : string (nifti file)
           Mask obtained from normalized preprocessed image

    Order of commands:

    - Deobliqing the scans.  For details see `3drefit <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3drefit.html>`_::

        3drefit -deoblique rest_3dc.nii.gz

    - Re-orienting the Image into Right-to-Left Posterior-to-Anterior Inferior-to-Superior (RPI) orientation. For details see `3dresample <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dresample.html>`_::

        3dresample -orient RPI
                   -prefix rest_3dc_RPI.nii.gz
                   -inset rest_3dc.nii.gz

    - Calculate voxel wise statistics. Get the RPI Image with mean intensity values over all timepoints for each voxel. For details see `3dTstat <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dTstat.html>`_::

        3dTstat -mean
                -prefix rest_3dc_RPI_3dT.nii.gz
                rest_3dc_RPI.nii.gz

    - Motion Correction. For details see `3dvolreg <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dvolreg.html>`_::

        3dvolreg -Fourier
                 -twopass
                 -base rest_3dc_RPI_3dT.nii.gz/
                 -zpad 4
                 -maxdisp1D rest_3dc_RPI_3dvmd1D.1D
                 -1Dfile rest_3dc_RPI_3dv1D.1D
                 -prefix rest_3dc_RPI_3dv.nii.gz
                 rest_3dc_RPI.nii.gz

      The base image or the reference image is the mean intensity RPI image obtained in the above the step.For each volume
      in RPI-oriented T2 image, the command, aligns the image with the base mean image and calculates the motion, displacement
      and movement parameters. It also outputs the aligned 4D volume and movement and displacement parameters for each volume.

    - Calculate voxel wise statistics. Get the motion corrected output Image from the above step, with mean intensity values over all timepoints for each voxel.
      For details see `3dTstat <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dTstat.html>`_::

        3dTstat -mean
                -prefix rest_3dc_RPI_3dv_3dT.nii.gz
                rest_3dc_RPI_3dv.nii.gz

    - Motion Correction and get motion, movement and displacement parameters. For details see `3dvolreg <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dvolreg.html>`_::

        3dvolreg -Fourier
                 -twopass
                 -base rest_3dc_RPI_3dv_3dT.nii.gz
                 -zpad 4
                 -maxdisp1D rest_3dc_RPI_3dvmd1D.1D
                 -1Dfile rest_3dc_RPI_3dv1D.1D
                 -prefix rest_3dc_RPI_3dv.nii.gz
                 rest_3dc_RPI.nii.gz

      The base image or the reference image is the mean intensity motion corrected image obtained from the above the step (first 3dvolreg run).
      For each volume in RPI-oriented T2 image, the command, aligns the image with the base mean image and calculates the motion, displacement
      and movement parameters. It also outputs the aligned 4D volume and movement and displacement parameters for each volume.

    - Create a brain-only mask. For details see `3dautomask <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dAutomask.html>`_::

        3dAutomask
                   -prefix rest_3dc_RPI_3dv_automask.nii.gz
                   rest_3dc_RPI_3dv.nii.gz

    - Edge Detect(remove skull) and get the brain only. For details see `3dcalc <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dcalc.html>`_::

        3dcalc -a rest_3dc_RPI_3dv.nii.gz
               -b rest_3dc_RPI_3dv_automask.nii.gz
               -expr 'a*b'
               -prefix rest_3dc_RPI_3dv_3dc.nii.gz

    - Normalizing the image intensity values. For details see `fslmaths <http://www.fmrib.ox.ac.uk/fsl/avwutils/index.html>`_::

        fslmaths rest_3dc_RPI_3dv_3dc.nii.gz
                 -ing 10000 rest_3dc_RPI_3dv_3dc_maths.nii.gz
                 -odt float

      Normalized intensity = (TrueValue*10000)/global4Dmean

    - Calculate mean of skull stripped image. For details see `3dTstat <http://afni.nimh.nih.gov/pub/dist/doc/program_help/3dTstat.html>`_::

        3dTstat -mean -prefix rest_3dc_RPI_3dv_3dc_3dT.nii.gz rest_3dc_RPI_3dv_3dc.nii.gz

    - Create Mask (Generate mask from Normalized data). For details see `fslmaths <http://www.fmrib.ox.ac.uk/fsl/avwutils/index.html>`_::

        fslmaths rest_3dc_RPI_3dv_3dc_maths.nii.gz
               -Tmin -bin rest_3dc_RPI_3dv_3dc_maths_maths.nii.gz
               -odt char

    .. exec::
        from CPAC.func_preproc import create_func_preproc
        wf = create_func_preproc()
        wf.write_graph(
            graph2use='orig',
            dotfilename='./images/generated/func_preproc.dot'
        )

    High Level Workflow Graph:

    .. image:: ../images/generated/func_preproc.png
       :width: 1000

    Detailed Workflow Graph:

    .. image:: ../images/generated/func_preproc_detailed.png
       :width: 1000

    Examples
    --------

    >>> import func_preproc
    >>> preproc = create_func_preproc(bet=True)
    >>> preproc.inputs.inputspec.func='sub1/func/rest.nii.gz'
    >>> preproc.run() #doctest: +SKIP


    >>> import func_preproc
    >>> preproc = create_func_preproc(bet=False)
    >>> preproc.inputs.inputspec.func='sub1/func/rest.nii.gz'
    >>> preproc.run() #doctest: +SKIP

    """

    preproc = pe.Workflow(name=wf_name)
    input_node = pe.Node(util.IdentityInterface(fields=['func',
                                                        'twopass',
                                                        'anatomical_brain_mask',
                                                        'anat_skull']),
                         name='inputspec')

    output_node = pe.Node(util.IdentityInterface(fields=['refit',
                                                         'reorient',
                                                         'reorient_mean',
                                                         'motion_correct',
                                                         'motion_correct_ref',
                                                         'movement_parameters',
                                                         'max_displacement',
                                                         'mask',
                                                         'skullstrip',
                                                         'func_mean',
                                                         'preprocessed',
                                                         'preprocessed_mask',
                                                         'slice_time_corrected',
                                                         'transform_matrices']),
                          name='outputspec')

    func_deoblique = pe.Node(interface=afni_utils.Refit(),
                             name='func_deoblique')
    func_deoblique.inputs.deoblique = True

    preproc.connect(input_node, 'func',
                    func_deoblique, 'in_file')

    func_reorient = pe.Node(interface=afni_utils.Resample(),
                            name='func_reorient')

    func_reorient.inputs.orientation = 'RPI'
    func_reorient.inputs.outputtype = 'NIFTI_GZ'

    preproc.connect(func_deoblique, 'out_file',
                    func_reorient, 'in_file')

    preproc.connect(func_reorient, 'out_file',
                    output_node, 'reorient')

    func_get_mean_RPI = pe.Node(interface=afni_utils.TStat(),
                                name='func_get_mean_RPI')

    func_get_mean_RPI.inputs.options = '-mean'
    func_get_mean_RPI.inputs.outputtype = 'NIFTI_GZ'

    preproc.connect(func_reorient, 'out_file',
                    func_get_mean_RPI, 'in_file')

    # calculate motion parameters
    func_motion_correct = pe.Node(interface=preprocess.Volreg(),
                                name='func_motion_correct_3dvolreg')
    func_motion_correct.inputs.zpad = 4
    func_motion_correct.inputs.outputtype = 'NIFTI_GZ'

    preproc.connect([(input_node, func_motion_correct, [(('twopass', collect_arguments, '-twopass', '-Fourier'),'args')]),])
    preproc.connect(func_get_mean_RPI, 'out_file',
                func_motion_correct, 'basefile')   

    preproc.connect(func_reorient, 'out_file',
                    func_motion_correct, 'in_file')

    func_get_mean_motion = func_get_mean_RPI.clone('func_get_mean_motion')
    preproc.connect(func_motion_correct, 'out_file',
                    func_get_mean_motion, 'in_file')

    preproc.connect(func_get_mean_motion, 'out_file',
                    output_node, 'motion_correct_ref')

    func_motion_correct_A = func_motion_correct.clone('func_motion_correct_A')
    func_motion_correct_A.inputs.md1d_file = 'max_displacement.1D'

    preproc.connect([
        (
            input_node, func_motion_correct_A, [
                (
                    ('twopass', collect_arguments, '-twopass', '-Fourier'),
                    'args'
                )]
        ),
    ])

    preproc.connect(func_reorient, 'out_file',
                    func_motion_correct_A, 'in_file')
    preproc.connect(func_get_mean_motion, 'out_file',
                    func_motion_correct_A, 'basefile')

    preproc.connect(func_motion_correct_A, 'out_file',
                    output_node, 'motion_correct')
    preproc.connect(func_motion_correct_A, 'md1d_file',
                    output_node, 'max_displacement')
    preproc.connect(func_motion_correct_A, 'oned_file',
                    output_node, 'movement_parameters')
    preproc.connect(func_motion_correct_A, 'oned_matrix_save',
                    output_node, 'transform_matrices')

    skullstrip_func = skullstrip_functional(skullstrip_tool, anatomical_mask_dilation, 
                                            "{0}_skullstrip".format(wf_name))

    preproc.connect(func_motion_correct_A, 'out_file',
                    skullstrip_func, 'inputspec.func')

    preproc.connect(input_node, 'anatomical_brain_mask',
                    skullstrip_func, 'inputspec.anatomical_brain_mask')

    preproc.connect(input_node, 'anat_skull',
                    skullstrip_func, 'inputspec.anat_skull')                


    preproc.connect(skullstrip_func, 'outputspec.func_brain',
                    output_node, 'skullstrip')

    preproc.connect(skullstrip_func, 'outputspec.func_brain_mask',
                    output_node, 'mask')

    func_mean = pe.Node(interface=afni_utils.TStat(),
                        name='func_mean')

    func_mean.inputs.options = '-mean'
    func_mean.inputs.outputtype = 'NIFTI_GZ'

    preproc.connect(skullstrip_func, 'outputspec.func_brain', 
                    func_mean, 'in_file')

    if n4_correction:
        func_mean_n4_corrected = pe.Node(interface = ants.N4BiasFieldCorrection(dimension=3, copy_header=True, bspline_fitting_distance=200), shrink_factor=2, 
                                        name='func_mean_n4_corrected')
        func_mean_n4_corrected.inputs.args = '-r True'
        # func_mean_n4_corrected.inputs.rescale_intensities = True
        preproc.connect(func_mean, 'out_file', 
                    func_mean_n4_corrected, 'input_image')
        preproc.connect(func_mean_n4_corrected, 'output_image',
                    output_node, 'func_mean')

    else:
        preproc.connect(func_mean, 'out_file',
                    output_node, 'func_mean')

    func_normalize = pe.Node(interface=fsl.ImageMaths(),
                             name='func_normalize')
    func_normalize.inputs.op_string = '-ing 10000'
    func_normalize.inputs.out_data_type = 'float'

    preproc.connect(skullstrip_func, 'outputspec.func_brain',
                    func_normalize, 'in_file')

    preproc.connect(func_normalize, 'out_file',
                    output_node, 'preprocessed')

    func_mask_normalize = pe.Node(interface=fsl.ImageMaths(),
                                  name='func_mask_normalize')
    func_mask_normalize.inputs.op_string = '-Tmin -bin'
    func_mask_normalize.inputs.out_data_type = 'char'

    preproc.connect(func_normalize, 'out_file',
                    func_mask_normalize, 'in_file')

    preproc.connect(func_mask_normalize, 'out_file',
                    output_node, 'preprocessed_mask')

    return preproc


def slice_timing_wf(name='slice_timing'):

    # allocate a workflow object
    wf = pe.Workflow(name=name)

    # configure the workflow's input spec
    inputNode = pe.Node(util.IdentityInterface(fields=['func_ts',
                                                       'tr',
                                                       'tpattern']),
                        name='inputspec')

    # configure the workflow's output spec
    outputNode = pe.Node(util.IdentityInterface(fields=['slice_time_corrected']),
                         name='outputspec')

    # create TShift AFNI node
    func_slice_timing_correction = pe.Node(interface=preprocess.TShift(),
                                           name='slice_timing')
    func_slice_timing_correction.inputs.outputtype = 'NIFTI_GZ'


    wf.connect([
        (
            inputNode,
            func_slice_timing_correction,
            [
                (
                    'func_ts',
                    'in_file'
                ),
                (
                    # add the @ prefix to the tpattern file going into
                    # AFNI 3dTshift - needed this so the tpattern file
                    # output from get_scan_params would be tied downstream
                    # via a connection (to avoid poofing)
                    ('tpattern', nullify, add_afni_prefix),
                    'tpattern'
                ),
                (
                    ('tr', nullify),
                    'tr'
                ),
            ]
        ),
    ])

    wf.connect(func_slice_timing_correction, 'out_file',
               outputNode, 'slice_time_corrected')

    return wf


def get_idx(in_files, stop_idx=None, start_idx=None):
    """
    Method to get the first and the last slice for
    the functional run. It verifies the user specified
    first and last slice. If the values are not valid, it
    calculates and returns the very first and the last slice

    Parameters
    ----------
    in_file : string (nifti file)
       Path to input functional run

    stop_idx : int
        Last volume to be considered, specified by user
        in the configuration file

    stop_idx : int
        First volume to be considered, specified by user
        in the configuration file

    Returns
    -------
    stop_idx :  int
        Value of first slice to consider for the functional run

    start_idx : int
        Value of last slice to consider for the functional run

    """

    # Import packages
    from nibabel import load

    # Init variables
    img = load(in_files)
    hdr = img.get_header()
    shape = hdr.get_data_shape()

    # Check to make sure the input file is 4-dimensional
    if len(shape) != 4:
        raise TypeError('Input nifti file: %s is not a 4D file' % in_files)
    # Grab the number of volumes
    nvols = int(hdr.get_data_shape()[3])

    if (start_idx == None) or (start_idx < 0) or (start_idx > (nvols - 1)):
        startidx = 0
    else:
        startidx = start_idx

    if (stop_idx == None) or (stop_idx > (nvols - 1)):
        stopidx = nvols - 1
    else:
        stopidx = stop_idx

    return stopidx, startidx


def connect_func_preproc(workflow, strat_list, c):

    from CPAC.func_preproc.func_preproc import create_func_preproc
    
    new_strat_list = []

    for num_strat, strat in enumerate(strat_list):

        nodes = strat.get_nodes_names()

        for skullstrip_tool in c.functionalMasking:
            
            skullstrip_tool = skullstrip_tool.lower()

            new_strat = strat.fork()

            func_preproc = create_func_preproc(
                skullstrip_tool=skullstrip_tool,
                n4_correction=c.n4_correct_mean_EPI,
                wf_name='func_preproc_%s_%d' % (skullstrip_tool, num_strat)
            )

            node, out_file = new_strat.get_leaf_properties()
            workflow.connect(node, out_file, func_preproc,
                            'inputspec.func')

            node, out_file = strat['anatomical_reorient']
            workflow.connect(node, out_file, func_preproc,
                            'inputspec.anat_skull')

            node, out_file = strat['anatomical_brain_mask']
            workflow.connect(node, out_file, func_preproc,
                            'inputspec.anatomical_brain_mask')

            func_preproc.inputs.inputspec.twopass = \
                getattr(c, 'functional_volreg_twopass', True)

            new_strat.append_name(func_preproc.name)
            new_strat.set_leaf_properties(func_preproc, 'outputspec.preprocessed')

            if 'gen_motion_stats_before_stc' in nodes: 
                
                new_strat.update_resource_pool({
                    'mean_functional': (func_preproc, 'outputspec.func_mean'),
                    'functional_preprocessed_mask': (func_preproc, 'outputspec.preprocessed_mask'),                              
                    'functional_preprocessed': (func_preproc, 'outputspec.preprocessed'),
                    'functional_brain_mask': (func_preproc, 'outputspec.mask'),
                    'motion_correct': (func_preproc, 'outputspec.motion_correct'),                                
                })
                
            else:

                new_strat.update_resource_pool({
                    'mean_functional': (func_preproc, 'outputspec.func_mean'),
                    'functional_preprocessed_mask': (func_preproc, 'outputspec.preprocessed_mask'),
                    'movement_parameters': (func_preproc, 'outputspec.movement_parameters'),
                    'max_displacement': (func_preproc, 'outputspec.max_displacement'),
                    'functional_preprocessed': (func_preproc, 'outputspec.preprocessed'),
                    'functional_brain_mask': (func_preproc, 'outputspec.mask'),
                    'motion_correct': (func_preproc, 'outputspec.motion_correct'),
                    'coordinate_transformation': (func_preproc, 'outputspec.transform_matrices'),
                })

            new_strat_list.append(new_strat)

    return workflow, new_strat_list
