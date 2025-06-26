header_sgp_fixed_wcs = """SIMPLE  =                    T / file does conform to FITS standard
BITPIX  =                   16 / number of bits per data pixel
NAXIS   =                    2 / number of data axes
NAXIS1  =                 4144 / length of data axis 1
NAXIS2  =                 2822 / length of data axis 2
BZERO   =                32768 / offset and data range to that of unsigned short
BSCALE  =                    1 / default scaling factor
CRPIX1  =                 2072 / reference spectrum pixel coordinate for axis 1
CRPIX2  =                 1411 / reference spectrum pixel coordinate for axis 2
CTYPE1  = 'RA---TAN'           / standard system and projection
CTYPE2  = 'DEC--TAN'           / standard system and projection
OBJECT  = 'M57     '           / Object name
DATE-LOC= '2020-05-30T02:22:49.0968820' / Local observation date
DATE-OBS= '2020-05-30T00:22:49.0968820' / UTC observation date
IMAGETYP= 'LIGHT   '           / Type of frame
CREATOR = 'Sequence Generator Pro v3.1.0.479' / Capture software
INSTRUME= 'ZWO ASI294MC Pro'   / Instrument name
OBSERVER= 'Benny   '           / Observer name
SITENAME= 'Ghent   '           / Observatory name
SITEELEV=                   10 / Elevation of the imaging site in meters
SITELAT = '51 3 0.000'         / Latitude of the imaging site in degrees
SITELONG= '3 43 0.000'         / Longitude of the imaging site in degrees
FOCUSER = 'ASCOM Driver for SestoSenso' / Focuser name
FOCPOS  =               827840 / Absolute focuser position
FOCTEMP =               14.162 / Focuser temperature
FWHEEL  = 'Manual Filter Wheel' / Filter Wheel name
FILTER  = 'HaOIII  '           / Filter name
EXPOSURE=                   30 / Exposure time in seconds
CCD-TEMP=                -17.5 / Camera cooler temperature
SET-TEMP=                  -18 / Camera cooler target temperature
XBINNING=                    1 / Camera X Bin
CCDXBIN =                    1 / Camera X Bin
YBINNING=                    1 / Camera Y Bin
CCDYBIN =                    1 / Camera Y Bin
TELESCOP= 'EQMOD ASCOM HEQ5/6' / Telescope name
RA      =     283.395539831101 / Object Right Ascension in degrees
DEC     =           33.0340625 / Object Declination in degrees
CRVAL1  =     283.395539831101 / RA at image center in degrees
CRVAL2  =           33.0340625 / DEC at image center in degrees
OBJCTRA = '18 53 34.930'       / Object Right Ascension in hms
OBJCTDEC= '+33 02 02.625'      / Object Declination in degrees
AIRMASS =      1.1136910218553 / Average airmass
OBJCTALT=     63.9199101820811 / Altitude of the object
CENTALT =     63.9199101820811 / Altitude of the object
FOCALLEN=                 1480 / The focal length of the telescope in mm
FLIPPED =                    F / Is image flipped
ANGLE   =               258.37 / Image angle
SCALE   =              0.65415 / Image scale (arcsec / pixel)
PIXSCALE=              0.65415 / Image scale (arcsec / pixel)
POSANGLE=     258.369995117188 / Camera rotator postion angle (degrees)
GAIN    =                  120 / Camera gain
EGAIN   =     3.99000000953674 / Electrons Per ADU
OFFSET  =                   30 / Camera offset
CUNIT1  = 'deg     '
CROTA1  =    78.36999511718801
CROTA2  =    78.36999511718801
CDELT1  = 0.000181708333333333
CDELT2  = 0.000181708333333333
CD1_1   = 3.66307430802135E-05
CD1_2   = 0.000177977827450975
CD2_1   = -0.00017797782745097
CD2_2   = 3.66307430802135E-05
END"""

header_maximdl = """SIMPLE  =                    T
BITPIX  =                   16 /8 unsigned int, 16 & 32 int, -32 & -64 real
NAXIS   =                    2 /number of axes
NAXIS1  =                 4864 /fastest changing axis
NAXIS2  =                 3232 /next to fastest changing axis
BSCALE  =   1.0000000000000000 /physical = BZERO + BSCALE*array_value
BZERO   =   32768.000000000000 /physical = BZERO + BSCALE*array_value
DATE-OBS= '2018-07-04T14:03:03' / [ISO 8601] UTC date/time of exposure start
EXPTIME =   3.00000000000E+002 / [sec] Duration of exposure
EXPOSURE=   3.00000000000E+002 / [sec] Duration of exposure
SET-TEMP=  -35.000000000000000 /CCD temperature setpoint in C
CCD-TEMP=  -34.993802250000002 /CCD temperature at start of exposure in C
XPIXSZ  =   7.4000000000000004 /Pixel Width in microns (after binning)
YPIXSZ  =   7.4000000000000004 /Pixel Height in microns (after binning)
XBINNING=                    1 / Binning level along the X-axis
YBINNING=                    1 / Binning level along the Y-axis
XORGSUBF=                    0 /Subframe X position in binned pixels
YORGSUBF=                    0 /Subframe Y position in binned pixels
READOUTM= 'Monochrome' /        Readout mode of image
FILTER  = 'SII     '           / Filter name
IMAGETYP= 'Light Frame' /       Type of image
SITELAT = '-31 16 24' /         Latitude of the imaging location
SITELONG= '149 03 52' /         Longitude of the imaging location
JD      =   2458304.0854513887 /Julian Date at start of exposure
TRAKTIME=   4.0000000000000000 /Exposure time used for autoguiding
FOCALLEN=   1425.0000000000000 /Focal length of telescope in mm
APTDIA  =   392.00000000000000 /Aperture diameter of telescope in mm
APTAREA =   120687.42673873901 /Aperture area of telescope in mm^2
SWCREATE= 'MaxIm DL Version 5.24 140126 0KJR5' /Name of software that created
the image
SBSTDVER= 'SBFITSEXT Version 1.0' /Version of SBFITSEXT standard in effect
OBJECT  = 'm17     '           / Target object name
TELESCOP= 'iTelescope 33'      / Telescope name
INSTRUME= 'Apogee USB/Net'     / Detector instrument name
OBSERVER= 'Benny Colyn'        / Observer name
NOTES   = '        '
FLIPSTAT= '        '
CSTRETCH= 'Medium  ' /          Initial display stretch mode
CBLACK  =                   59 /Initial display black level in ADUs
CWHITE  =                  981 /Initial display white level in ADUs
PEDESTAL=                 -100 /Correction to add for zero-based ADU
SWMODIFY= 'MaxIm DL Version 5.24 140126 0KJR5' /Name of software that modified
the image
HISTORY  Bias Subtraction (Bias 1, 4864 x 3232, Bin1 x 1, Temp -35C,
HISTORY  Exp Time 0ms)
CALSTAT = 'BDF     '
HISTORY  Dark Subtraction (Dark 10, 4864 x 3232, Bin1 x 1, Temp -35C,
HISTORY  Exp Time 300s)
HISTORY  Flat Field (Flat SII 1, SII, 4864 x 3232, Bin1 x 1, Temp -35C,
HISTORY  Exp Time 5s)
HISTORY  Flat-Bias(Bias 1,4864 x 3232,Bin1 x 1,Temp -35C,Exp Time 0ms)
USERNAME= 'bcolyn  '
HIERARCH iTelescope = 'iTelescope 33'
HIERARCH iTelescopePlateScaleH = '1.07352112676056'
HIERARCH iTelescopePlateScaleV = '1.07352112676056'
DATE-HP = '2018-07-04T14:03:03.414'
PA      = '270.693881052116'
PIERSIDE= 'EAST    '
HISTORY File was processed by PinPoint 5.1.8 at 2018-07-04T14:08:45
DATE    = '04/07/18'           / [old format] UTC date of exposure start
TIME-OBS= '14:03:03'           / [old format] UTC time of exposure start
UT      = '14:03:03'           / [old format] UTC time of exposure start
TIMESYS = 'UTC     '           / Default time system
RADECSYS= 'FK5     '           / Equatorial coordinate system
AIRMASS =   1.04162552350E+000 / Airmass (multiple of zenithal airmass)
ST      = '18 55 09.27'        / Local apparent sidereal time of exp. start
LAT-OBS =  -3.12736111111E+001 / [deg +N WGS84] Geodetic latitude
LONG-OBS=   1.49064444444E+002 / [deg +E WGS84] Geodetic longitude
ALT-OBS =   1.16500000000E+003 / [metres] Altitude above mean sea level
OBSERVAT= 'iTelescope.Net T33 SSO' / Observatory name
RA      = '18 20 48.12'        / [hms J2000] Target right ascension
OBJCTRA = '18 20 48.12'        / [hms J2000] Target right ascension
DEC     = '-16 10 58.8'        / [dms +N J2000] Target declination
OBJCTDEC= '-16 10 58.8'        / [dms +N J2000] Target declination
CLRBAND = 'R       '           / [J-C std] Std. color band of image or C=Color
END"""

header_apt = """SIMPLE  =                    T / file does conform to FITS standard
BITPIX  =                   16 / number of bits per data pixel
NAXIS   =                    2 / number of data axes
NAXIS1  =                 4144 / length of data axis 1
NAXIS2  =                 2822 / length of data axis 2
EXTEND  =                    T / FITS dataset may contain extensions
COMMENT   FITS (Flexible Image Transport System) format is defined in 'Astronomy
COMMENT   and Astrophysics', volume 376, page 359; bibcode: 2001A&A...376..359H
BZERO   =                32768 / offset data range to that of unsigned short
BSCALE  =                    1 / default scaling factor
OBJECT  = 'TEST    '           / The name of Object Imaged
TELESCOP= 'EQMOD HEQ5/6'       / The Telescope used
INSTRUME= 'ZWO ASI294MC Pro'   / The model Camera used
DATE-OBS= '2018-09-17T21:04:46' / The UTC date and time at the start of the expo
HIERARCH CAMERA-DATE-OBS = '2018-09-17T21:04:46' / The UTC date and time at the
EXPTIME =                 240. / The total exposure time in seconds
XPIXSZ  =                 4.63 / Pixel width in microns (after binning)
YPIXSZ  =                 4.63 / Pixel height in microns (after binning)
XBINNING=                    1 / Binning factor in width
YBINNING=                    1 / Binning factor in height
XORGSUBF=                    0 / Sub frame X position
YORGSUBF=                    0 / Sub frame Y position
EGAIN   =     1.00224268436432 / Electronic gain in e-/ADU
FOCALLEN=                  400 / Focal Length of the Telescope in mm
JD      =     2458379.38114583 / Julian Date
SWCREATE= 'Astro Photography Tool - APT v.3.54.1' / Imaging software
SBSTDVER= 'SBFITSEXT Version 1.0' / Standard version
SNAPSHOT=                    1 / Number of images combined
SET-TEMP=                 -15. / The setpoint of the cooling in C
IMAGETYP= 'Light Frame'        / The type of image
OBJCTRA = '01 35 00'           / The Right Ascension of the center of the image
OBJCTDEC= '+30 44 25'          / The Declination of the center of the image
SITELAT = '+51 03 00.000'      / The site Latitude
SITELONG= '+03 41 00.000'      / The site Longitude
GAIN    =                  120 / The gain set (if supported)
"""

header_sharpcap = """SIMPLE  =                    T / Created by ImageJ FITS_Writer
BITPIX  =                   16 / number of bits per data pixel
NAXIS   =                    2 / number of data axes
NAXIS1  =                 1936 / length of data axis 1
NAXIS2  =                 1088 / length of data axis 2
BZERO   = 32768.0              / data range offset
BSCALE  = 1.0                  / scaling factor
SWCREATE= 'SharpCap'           /
CCD-TEMP=                 12.7 /
YBINNING=                    1 /
XBINNING=                    1 /
YPIXSZ  =     2.90000009536743 /
EXPTIME =                    2 /
DATE-OBS= '2019-04-29T20:25:29.8219583' / System Clock-Frame Grabbed
EXTEND  =                    T / Extensions are permitted
XPIXSZ  =     2.90000009536743 /
INSTRUME= 'ZWO ASI290MM Mini'  /
HISTORY WCS created by AIJ link to Astronomy.net website
HISTORY WCS created on 2019-04-30T15:36:36.057
WCSAXES =                    2 / no comment
CTYPE1  = 'RA---TAN-SIP' / TAN (gnomic) projection + SIP distortions
CTYPE2  = 'DEC--TAN-SIP' / TAN (gnomic) projection + SIP distortions
CUNIT1  = 'deg     ' / X pixel scale units
CUNIT2  = 'deg     ' / Y pixel scale units
EQUINOX =               2000.0 / Equatorial coordinates definition (yr)
LONPOLE =                180.0 / no comment
LATPOLE =                  0.0 / no comment
CRVAL1  =        251.337418297 / RA  of reference point
CRVAL2  =         89.049177068 / DEC of reference point
CRPIX1  =                  969 / X reference pixel
CRPIX2  =                  545 / Y reference pixel
CD1_1   =    0.000389383715878 / Transformation matrix
CD1_2   =    0.000788738076566 / no comment
CD2_1   =   -0.000787298612487 / no comment
CD2_2   =    0.000389062499079 / no comment
IMAGEW  =                 1936 / Image width,  in pixels.
IMAGEH  =                 1088 / Image height, in pixels.
A_ORDER =                    2 / Polynomial order, axis 1
A_0_2   =    5.53957795846E-07 / no comment
A_1_1   =   -1.23639820451E-06 / no comment
A_2_0   =    3.75738837922E-07 / no comment
B_ORDER =                    2 / Polynomial order, axis 2
B_0_2   =   -9.07325128016E-07 / no comment
B_1_1   =   -8.18595386612E-07 / no comment
B_2_0   =    6.70695855884E-08 / no comment
AP_ORDER=                    2 / Inv polynomial order, axis 1
AP_0_1  =   -4.22921490886E-07 / no comment
AP_0_2  =   -5.53956254374E-07 / no comment
AP_1_0  =    3.47430115538E-07 / no comment
AP_1_1  =    1.23639416481E-06 / no comment
AP_2_0  =   -3.75739055863E-07 / no comment
BP_ORDER=                    2 / Inv polynomial order, axis 2
BP_0_1  =    2.51121369507E-07 / no comment
BP_0_2  =    9.07318637702E-07 / no comment
BP_1_0  =    3.46814187446E-07 / no comment
BP_1_1  =    8.18588486507E-07 / no comment
BP_2_0  =   -6.70689383923E-08 / no comment
"""

header_nina = """SIMPLE  =                    T / C# FITS
BITPIX  =                   16 /
NAXIS   =                    2 / Dimensionality
NAXIS1  =                 4144 /
NAXIS2  =                 2822 /
BZERO   =                32768 /
EXTEND  =                    T / Extensions are permitted
IMAGETYP= 'LIGHT'              / Type of exposure
EXPOSURE=                 30.0 / [s] Exposure duration
EXPTIME =                 30.0 / [s] Exposure duration
DATE-LOC= '2021-03-02T21:19:00.455' / Time of observation (local)
DATE-OBS= '2021-03-02T20:19:00.455' / Time of observation (UTC)
XBINNING=                    1 / X axis binning factor
YBINNING=                    1 / Y axis binning factor
GAIN    =                  120 / Sensor gain
OFFSET  =                   30 / Sensor gain offset
EGAIN   =     1.00224268436432 / [e-/ADU] Electrons per A/D unit
XPIXSZ  =                 4.63 / [um] Pixel X axis size
YPIXSZ  =                 4.63 / [um] Pixel Y axis size
INSTRUME= 'ZWO ASI294MC Pro'   / Imaging instrument name
SET-TEMP=                -10.0 / [degC] CCD temperature setpoint
CCD-TEMP=                -10.0 / [degC] CCD temperature
BAYERPAT= 'RGGB'               / Sensor Bayer pattern
XBAYROFF=                    0 / Bayer pattern X axis offset
YBAYROFF=                    0 / Bayer pattern Y axis offset
USBLIMIT=                   40 / Camera-specific USB setting
OBJECT  = 'MarsM45 '           / Name of the object of interest
OBJCTRA = '00 00 00'           / [H M S] RA of imaged object
OBJCTDEC= '+00 00 00'          / [D M S] Declination of imaged object
ROWORDER= 'TOP-DOWN'           / FITS Image Orientation
EQUINOX =               2000.0 / Equinox of celestial coordinate system
SWCREATE= 'N.I.N.A. 1.10.2.90' / Software that created this file
"""

header_dwarflab = """SIMPLE  =                    T / file does conform to FITS standard
BITPIX  =                   16 / number of bits per data pixel
NAXIS   =                    2 / number of data axes
NAXIS1  =                 1920 / length of data axis 1
NAXIS2  =                 1080 / length of data axis 2
EXTEND  =                    T / FITS dataset may contain extensions
BZERO   =                32768 / offset data range to that of unsigned short
BSCALE  =                    1 / default scaling factor
DATE-OBS= '2023-06-10T21:38:19.442' / Time start to exposure
DATE    = '2023-06-10T21:38:35.938' / Fits written to sd card
EXP     =                  15. / [s] Sub Exposure Time
GAIN    =                  110 / Sub Gain
BINNING = '2*2     '           / n*n pixels add together
RA      =                   0. / [h] Observe object RA coordinate (J2000)
DEC     =                   0. / [deg] Observe object DEC coordinate (J2000)
OBJECT  = '        '           / Observe object
BAYERPAT= 'GBRG    '           / Bayer pattern
TELESCOP= 'DWARFII '           / DWARF II telescope
INSTRUME= 'DWARFII '           / DWARF II instrument
ORIGIN  = 'DWARFLAB'           / DWARF II's creator
"""

header_app = """
SIMPLE  =                    T / Java FITS: Mon Aug 20 13:26:55 CEST 2018       
BITPIX  =                  -32 / bits per data value                            
NAXIS   =                    2 / number of axes                                 
NAXIS1  =                 4009 / size of the n'th axis                          
NAXIS2  =                 2674 / size of the n'th axis                          
EXTEND  =                    T / Extensions are permitted                       
BSCALE  =                  1.0 / scale factor                                   
BZERO   =                  0.0 / no offset                                      
DATE    = '2018-08-20T11:28:45' / creation date of Integration                  
SOFTWARE= 'Astro Pixel Processor by Aries Productions' / software               
VERSION = '1.062   '           / Astro Pixel Processor version                  
INTEGRAT= 'Integration'        / integration of light frames                    
CFAIMAGE= 'no      '           / Color Filter Array pattern                     
GAIN    =                  0.0 / gain or ISO depending on instrument            
EXPTIME =                900.0 / exposure time (s)                              
LOK-1   = ' 9,78E-03'          / lokation of channel 1                          
SCALE-1 = ' 1,86E-03'          / dispersion of channel 1                        
NOISE-1 = ' 9,41E-04'          / noise level of channel 1                       
SNR-1   = ' 4,33E+00'          / Signal to Noise Ratio of channel 1             
medNR-1 = ' 1,05E+00'          / median noise reduction, channel 1              
medENR-1= ' 1,15E+00'          / effective median noise reduction, channel 1    
refNR-1 = ' 1,06E+00'          / reference noise reduction, channel 1           
refENR-1= ' 1,18E+00'          / effective reference noise reduction, channel 1 
END                                                                             
"""

header_app2 = """SIMPLE  =                    T / Java FITS: Sat Mar 09 12:54:56 CET 2024        
BITPIX  =                  -32 / bits per data value                            
NAXIS   =                    2 / number of axes                                 
NAXIS1  =                 4144 / size of the n'th axis                          
NAXIS2  =                 2822 / size of the n'th axis                          
EXTEND  =                    T / Extensions are permitted                       
BSCALE  =                  1.0 / scale factor                                   
BZERO   =                 0.E0 / no offset                                      
DATE    = '2024-03-09T11:57:12' / creation date of MasterFlat                   
SOFTWARE= 'Astro Pixel Processor by Aries Productions' / software               
VERSION = '2.0.0-beta28'       / Astro Pixel Processor version                  
CALFRAME= 'MasterFlat'         / master flat frame                              
INSTRUME= 'ZWO ASI294MC Pro'   / instrument name                                
CFAIMAGE= 'RGGB    '           / Color Filter Array pattern                     
ROWORDER= 'TOP-DOWN'           / Order of pixel rows stored in the image array  
NOTE-1  = 'INTEGRATION METADATA'                                                
EXPTIME =                 1.28 / exposure time (s)                              
GAIN    =                120.0 / gain or ISO depending on instrument            
NORMMETH= 'multiply-scale'     / normalization method                           
NORMSCAL= 'BWMV    '           / normalization scale/dispersion calculation     
FLATBLUR= 'no blur '           / blur method of masterflat                      
INT-METH= 'median  '           / integration method                             
OUTL-REJ= 'adaptive rejection' / outlier rejection filter                       
OUTL-KL =                7.8E0 / outlier rejection kappa low                    
OUTL-KH =                3.8E0 / outlier rejection kappa high                   
LNC-DEG = 'noLNC   '           / Local Normalization Correction not applied     
AD-PED  =                 0.E0 / adaptive pedestal from data calibration        
FILTNUM =                    1 / data combined from 1 filter                    
FILT-1  = 'HaOIII  '           / filter used                                    
SESSNUM =                    1 / data combined from 1 session                   
SESS-1  = 'session 1'          / capture session                                
CALIB-1 =   0.2017456293106079 / calibration value of channel 1                 
CALIB-2 =   0.5788752734661102 / calibration value of channel 2                 
CALIB-3 =   0.3124261200428009 / calibration value of channel 3                 
LOK-R   =      1.8618245423E-1 / lokation of channel-R                          
LOK-G1  =      5.5874276161E-1 / lokation of channel-G1                         
LOK-G2  =      5.5874276161E-1 / lokation of channel-G2                         
LOK-B   =       2.910823524E-1 / lokation of channel-B                          
SCALE-R =      8.8843654025E-3 / dispersion/scale of channel-R                  
SCALE-G1=      2.1128164353E-2 / dispersion/scale of channel-G1                 
SCALE-G2=      2.1128164353E-2 / dispersion/scale of channel-G2                 
SCALE-B =      1.1176436909E-2 / dispersion/scale of channel-B                  
NOISE-R =      1.3586640125E-3 / MRS gaussian noise estimate of channel-R       
NOISE-G1=      2.8296592645E-3 / MRS gaussian noise estimate of channel-G1      
NOISE-G2=      2.8174801264E-3 / MRS gaussian noise estimate of channel-G2      
NOISE-B =      1.9723966252E-3 / MRS gaussian noise estimate of channel-B       
NUMFRAME=                   30 / # number of frames used in MasterFlat creation 
END                                                                             
"""