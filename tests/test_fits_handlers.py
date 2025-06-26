from datetime import datetime
from astropy.io.fits import Header

from tests.utils import fix_embedded_header
from .sample_headers import *
from photonfinder.models import File
from photonfinder.fits_handlers import normalize_fits_header


class TestNormalizeFitsHeader:
    """Tests for the normalize_fits_header function."""

    @staticmethod
    def create_test_file():
        """Create a test File object for testing."""
        return File(
            root=None,
            path="test_path",
            name="test_file.fits",
            size=1000,
            mtime_millis=0
        )

    def test_sgp_header(self):
        file = self.create_test_file()

        sgp_header = """SIMPLE  =                    T / file does conform to FITS standard             
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
END""".replace('\r\n', '\n')

        # Process the header
        header_bytes = fix_embedded_header(sgp_header)
        # Test header round-tripping
        parsed = Header.fromstring(header_bytes)
        serialized = bytes(parsed.tostring(), "ascii")
        assert header_bytes == serialized, "Header round-tripping failed"

        header = Header.fromstring(header_bytes)
        image = normalize_fits_header(file, header)

        assert image is not None
        assert image.image_type == 'LIGHT'
        assert image.filter == 'HaOIII'
        assert image.exposure == 30.0
        assert image.gain == 120
        assert image.binning == 1
        assert image.set_temp == -18.0
        assert image.camera == 'ZWO ASI294MC Pro'
        assert image.telescope == 'EQMOD ASCOM HEQ5/6'
        assert image.object_name == 'M57'
        expected_date = datetime(2020, 5, 30, 0, 22, 49, 96882)
        assert isinstance(image.date_obs, datetime)
        assert image.date_obs == expected_date

        # Assert coordinate fields
        assert image.coord_ra is not None
        assert image.coord_dec is not None
        assert image.coord_pix256 is not None
        assert isinstance(image.coord_ra, float)
        assert isinstance(image.coord_dec, float)
        assert isinstance(image.coord_pix256, int)
        assert round(image.coord_ra, 6) == round(283.395539831101, 6)  # RA from header
        assert round(image.coord_dec, 6) == round(33.0340625, 6)  # DEC from header

    def test_nina_header(self):
        """Test processing a NINA FITS header."""
        file = self.create_test_file()

        nina_header = """SIMPLE  =                    T / C# FITS                                        
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
END"""

        header_bytes = fix_embedded_header(nina_header)
        header = Header.fromstring(header_bytes)
        image = normalize_fits_header(file, header)

        assert image is not None
        assert image.image_type == 'LIGHT'
        assert image.filter is None
        assert image.exposure == 30.0
        assert image.gain == 120
        assert image.binning == 1
        assert image.set_temp == -10.0
        assert image.camera == 'ZWO ASI294MC Pro'
        assert image.telescope is None
        assert image.object_name == 'MarsM45'
        expected_date = datetime(2021, 3, 2, 20, 19, 0, 455000)
        assert isinstance(image.date_obs, datetime)
        assert image.date_obs == expected_date

        # Assert coordinate fields, everything 0 is the same as missing
        assert image.coord_ra is None
        assert image.coord_dec is None
        assert image.coord_pix256 is None

    def test_sharpcap_header(self):
        """Test processing a SharpCap FITS header."""
        file = self.create_test_file()
        sharpcap_header = """SIMPLE  =                    T / C# FITS: 03/20/2025 00:04:01                   
BITPIX  =                   32                                                  
NAXIS   =                    3 / Dimensionality                                 
NAXIS1  =                 5496                                                  
NAXIS2  =                 3672                                                  
NAXIS3  =                    3                                                  
EQUINOX =   2025.2154993242248 /                                                
GAIN    =                  300 /                                                
OBJECT  = 'Markarian'          /                                                
OFFSET  =                    8 /                                                
BLKLEVEL=                    8 /                                                
SUBEXP  =                    8 /                                                
CAMID   = '112E900511080900'   /                                                
PIERSIDE= 'WEST'               /                                                
AIRMASS =    1.332408494005141 / Pickerings formula from target elevation only. 
OBJCTAZ =        149.763599444 /                                                
OBJCTDEC= '+12 53 33.000'      / Epoch : JNOW                                   
SET-TEMP=                    0 /                                                
EGAIN   =               0.1226 / Electrons per ADU at true ADC bit depth        
ADCBITS =                   12 / Bit depth of camera sensor ADC in current mode 
BIASADU =     1082.87451171875 / ADU for bias level (no photons) at current sett
EGAINSAV=              0.00766 / Electrons per ADU at saved bit depth           
RELGAIN =               30.814 / Multiplicative gain relative to minumum        
OBJCTALT=        48.5618985732 /                                                
OBJCTRA = '12 29 23.000'       / Epoch : JNOW                                   
FOCALLEN=                312.5 /                                                
RA      =       187.3498863705 / Epoch : JNOW                                   
EXTEND  =                    T / Extensions are permitted                       
BSCALE  =                    1 /                                                
ROWORDER= 'TOP-DOWN'           /                                                
EXPTIME =                  280 / seconds                                        
XPIXSZ  =                  2.4 / microns, includes binning if any               
YPIXSZ  =                  2.4 / microns, includes binning if any               
XBINNING=                    1 /                                                
YBINNING=                    1 /                                                
DEC     =        12.8925716003 / Epoch : JNOW                                   
CCD-TEMP=                 -0.5 / C                                              
SWCREATE= 'SharpCap v4.1.12946.0, 64 bit' /                                     
DATE-OBS= '2025-03-19T22:51:59.3846323' / System Clock:Est. Frame Start         
DATE-END= '2025-03-19T23:03:40.4689935' / System Clock:Est. Frame End           
DATE-AVG= '2025-03-19T22:57:49.9268129' / System Clock:Est. Frame Mid Point     
JD_UTC  =   2460754.4568278566 / Julian Date at mid exposure                    
RDNOISE =                  1.7 / Read noise in electrons                        
COLORTYP= 'RGB'                /                                                
INSTRUME= 'ZWO ASI183MC Pro'   /                                                
END                                                                             """

        header_bytes = fix_embedded_header(sharpcap_header)
        header = Header.fromstring(header_bytes)
        image = normalize_fits_header(file, header)

        assert image is not None
        assert image.image_type is None  # Default value since SharpCap doesn't have IMAGETYP
        assert image.filter is None  # SharpCap header doesn't have a FILTER keyword
        assert image.exposure == 280.0  # From EXPTIME
        assert image.gain == 300
        assert image.binning == 1
        assert image.set_temp == 0.0
        assert image.camera == 'ZWO ASI183MC Pro'
        assert image.telescope is None
        assert image.object_name == 'Markarian'
        expected_date = datetime(2025, 3, 19, 22, 51, 59, 384632)
        assert isinstance(image.date_obs, datetime)
        assert image.date_obs == expected_date
        assert image.offset == 8
        # Assert coordinate fields
        assert image.coord_ra is not None
        assert image.coord_dec is not None
        assert image.coord_pix256 is not None
        assert isinstance(image.coord_ra, float)
        assert isinstance(image.coord_dec, float)
        assert isinstance(image.coord_pix256, int)
        assert round(image.coord_ra, 6) == round(187.3498863705, 6)  # RA from header
        assert round(image.coord_dec, 6) == round(12.8925716003, 6)  # DEC from header

    def test_dwarflab_header(self):
        header = Header.fromstring(header_dwarflab, "\n")
        file = self.create_test_file()
        image = normalize_fits_header(file, header)
        assert image is not None
        assert image.binning == 2

    def test_app_header(self):
        header = Header.fromstring(header_app, "\n")
        file = self.create_test_file()
        image = normalize_fits_header(file, header)
        assert image is not None
        assert image.image_type == "MASTER LIGHT"
        assert image.exposure == 900

        header = Header.fromstring(header_app2, "\n")
        file = self.create_test_file()
        image = normalize_fits_header(file, header)
        assert image is not None
        assert image.image_type == "MASTER FLAT"
        assert image.filter == "HaOIII"
