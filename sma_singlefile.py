"""
salford_mic_arc - a Python package for reading directivity measurement data
https://github.com/fchirono/salford_mic_arc
Copyright (c) 2022, Fabio Casagrande Hirono


Classes and functions to process a single HDF5 file from Dewesoft.

Author:
    Fabio Casagrande Hirono
    November 2022
"""

import h5py
import numpy as np

import soundfile as sf
import scipy.signal as ss


from sma_consts_aux import P_REF, DEFAULT_NDFT, DEFAULT_NOVERLAP, \
    DEFAULT_WINDOW, _calc_spectral_centroid, _round_to_nearest_odd


# #############################################################################
# %% Class 'SingleFileTimeSeries'
# #############################################################################

class SingleFileTimeSeries:
    """
    Class to read raw measurement data from Dewesoft HDF5 files
    """

    # *************************************************************************
    def __init__(self, filename, mic_channel_names, T=30, fs=50000,
                 other_ch_names=None, fs2=None):

        # name of file to be read (must be HDF5)
        self.filename = filename

        # list of microphone channels' names in 'filename'
        self.mic_channel_names = mic_channel_names
        self.N_ch = len(self.mic_ch_names)

        # nominal duration of data recording, in seconds
        #   float
        self.T = T

        # default sampling freq
        #   float
        self.fs = fs

        # time vector
        #   (T*fs,) array
        self.t = np.linspace(0, self.T - 1/self.fs, self.T*self.fs)

        # 2nd sampling freq, for data acquired with SIRIUSiwe STG-M rack unit
        # (e.g. load cell, thermocouple)
        if fs2:
            #   float
            self.fs2 = fs2

            #   (T*fs2,) array
            self.t2 = np.linspace(0, self.T - 1/self.fs2, self.T*self.fs2)

        # read mic data from filename
        self._read_mic_chs(filename, mic_channel_names)

        # if present, read other channels' data from 'filename'
        if other_ch_names:
            # list of non-acoustic channels in 'filename'
            self.other_ch_names = other_ch_names
            self._read_other_chs(filename, other_ch_names)


    # *************************************************************************
    def _read_mic_chs(self, filename, mic_ch_names):
        """
        Reads microphone data from a HDF5 file generated by Dewesoft. Data
        length and sampling frequencies are defined at initialisation.

        Parameters
        ----------
        filename : str
            String containing path and filename to be read. Must be in HDF5
            format.

        mic_ch_names : list
            List of strings containing the names of the 10 microphone channels
            as set up in DewesoftX.
        """

        with h5py.File(filename, 'r') as h5file:

            # -----------------------------------------------------------------
            # check for recording length of 1st mic channel, in case actual data is
            # shorter than (T*fs) samples
            rec_length = h5file[mic_ch_names[0]].shape[0]
            assert rec_length <= self.T*self.fs, \
                "Actual hdf5 file data length is longer than 'T*fs' declared for this instance!"

            # -----------------------------------------------------------------
            # assert all mic channel names actually exist in h5file
            channel_names = list(h5file.keys())

            assert set(mic_ch_names).issubset(channel_names), \
                "Channel named {} does not exist in this hdf5 file!"

            self.mic_data = np.zeros((self.N_ch, self.T*self.fs))

            # read mic data from HDF5 file
            for ch_index, ch_name in enumerate(mic_ch_names):
                self.mic_data[ch_index, :rec_length] = h5file[ch_name][:, 1]

            # -----------------------------------------------------------------


    # *************************************************************************
    def _read_other_chs(self, filename, other_ch_names):
        """
        Reads other channels' data from a HDF5 file generated by Dewesoft.
        Data length and sampling frequencies are defined at initialisation.

        Parameters
        ----------
        filename : str
            String containing path and filename to be read.

        other_ch_names : list
            List of strings containing the names of the other channels
            in DewesoftX - e.g. 'RPM', 'Temperature', 'LoadCell', etc.

        """

        with h5py.File(filename, 'r') as h5file:

            # assert all channel names actually exist in h5file
            channel_names = list(h5file.keys())

            assert set(other_ch_names).issubset(channel_names), \
                "Channel named {} does not exist in this hdf5 file!"

            # read data from HDF5 file, save as attribute
            for ch_name in other_ch_names:
                data = h5file[ch_name][:, 1]
                setattr(self, ch_name, data)


    # *************************************************************************
    def calc_chs_mean(self, ch_names):
        """
        Iterates over a list of channel names and calculates their mean value
        over time. Generally used for non-acoustic data - e.g. temperature,
        load cells, etc.

        For each channel named 'xx', stores the result in a new
        attribute named 'mean_xx'.

        Parameters
        ----------
        other_ch_names : list
            List of strings containing the names of the other channels
            in DewesoftX - e.g. 'RPM', 'Temperature', 'LoadCell', etc.
        """

        for name in ch_names:
            assert hasattr(self, name), \
                "Channel {} does not exist in this SingleFileTimeSeries instance!".format(name)

            mean_value = np.mean( getattr(self, name))
            setattr(self, 'mean_' + name, mean_value)


    # *************************************************************************
    def filter_data(self, filter_order=3, fc=50, btype='highpass'):
        """
        Filter time-domain microphone data at given filter order, cutoff
        frequency(ies), filter type, and overwrite result over original data.
        Uses Butterworth filter topology, and applies fwd-bkwd filtering.
        """

        my_filter = ss.butter(filter_order, fc, btype,
                              output='sos', fs=self.fs)

        for ch in range(self.N_ch):
            # fwd-bkwd filtering of the signals
            hp_data = ss.sosfiltfilt(my_filter, self.mic_data[ch, :])

            # overwrite original data
            self.mic_data[ch, :] = hp_data


    # *************************************************************************
    def estimate_peak_freq(self, f_low, f_high, Ndft=2**14):
        """
        Estimates the centre frequency of the tallest peak in the spectrum
        within a given frequency range [f_low, f_high] (in Hz). Estimate is
        averaged across all channels.

        Uses a "spectral centroid" calculation, so it can estimate values in
        between frequency samples.
        """

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        df = self.fs/Ndft
        freq = np.linspace(0, self.fs - df, Ndft)[:Ndft//2+1]

        PSDs = self.calc_PSDs(Ndft, window='hann', Noverlap=Ndft//2)

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        # calculate broadband component of PSD to use as amplitude threshold
        median_kernel_Hz = 100      # [Hz]
        PSDs.calc_broadband_PSD(median_kernel_Hz, units='Hz')

        # select freqs between f_low and f_high
        freq_mask = (freq >= f_low) & (freq <= f_high)

        # freq index of first mask entry
        mask_index = np.argwhere(freq_mask == 1)[0][0]

        f_peak = np.zeros(PSDs.N_ch)

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        for ch in range(PSDs.N_ch):

            # find tallest peak within freq range
            peak_index, peak_properties = ss.find_peaks(PSDs.psd[ch, freq_mask],
                                                        height=PSDs.psd_broadband[ch, freq_mask])

            # -----------------------------------------------------------------
            # if no peaks were found, write NaN in peak freq
            if (peak_properties['peak_heights']).size == 0:
                f_peak[ch] = np.nan

            # -----------------------------------------------------------------
            # if one or more peaks were found....
            else:
                n_tallest = np.argmax(peak_properties['peak_heights'])

                # list of indices for peak freq over all channels
                n_peak = mask_index + peak_index[n_tallest]

                # calculate spectral centroid around tallest peak to improve
                # estimate of peak frequency
                search_radius = 2
                fpeak_mask = np.arange(n_peak - search_radius,
                                       n_peak + search_radius + 1)

                f_peak[ch] = _calc_spectral_centroid(PSDs.freq[fpeak_mask],
                                                     PSDs.psd[ch, fpeak_mask])
            # -----------------------------------------------------------------

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        return np.nanmean(f_peak)


    # *************************************************************************
    def calc_PSDs(self, Ndft=DEFAULT_NDFT, Noverlap=DEFAULT_NOVERLAP,
                  window=DEFAULT_WINDOW, t0=0):
        """
        Calculates and outputs the PSDs of all channels. Optionally, skip
        initial segment 't0'.
        """

        n = t0*self.fs

        PSDs = np.zeros((self.N_ch, Ndft//2+1))
        for ch in range(self.N_ch):
            freq, PSDs[ch, :] = ss.welch(self.mic_data[ch, n:], self.fs,
                                         window=window, nperseg=Ndft,
                                         noverlap=Noverlap)

        myPSDs = SingleFilePSD(self.filename, PSDs, freq, self.fs, Ndft,
                               Noverlap, window)

        return myPSDs


    # *************************************************************************
    def export_wavs(self, wav_filename, channels=10, subtype='FLOAT'):
        """
        Exports current 'mic_data' time series as a multichannel .WAV file.
        Requires 'soundfile' (previously 'pysoundfile') package.

        Parameters
        ----------
        wav_filename : string
            File name of multichannel .wav file.

        channels : int or list, optional
            Channels to output. If 'int', outputs this many channels in
            ascending order; if list, output channel values contained in list,
            in the given order.

        subtype : string, optional
            String defining .wav file subtype. Use
            'soundfile.available_subtypes()' to list current options. Default
            value is 'FLOAT' for 32-bit float.

        Returns
        -------
        None.

        Notes
        -----
        Maximum value allowed in 'channels' is 10.

        If 'channel=8', the output will be a 8-channel .wav file containing
        data from mics index 0 to 7.

        If 'channels=[2, 6, 5]', the output will be a 3-channel .wav file
        containing data from mics 2, 6 and 5, in that order.
        """

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        # check all mic values are <=1, print warning if not
        if not (np.abs(self.mic_data)<=1).all():
            print("WARNING: Some microphone signal amplitudes are above unity!")

        # checks filename ends in '.wav' extension, add if it doesn't
        if wav_filename[-4:] != '.wav':
            wav_filename += '.wav'

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        # check whether 'channels' is int, if so create channel list
        if isinstance(channels, int):
            assert channels<=10, \
                "If int, 'channels' must be equal to or less than 10!"
            ch_list = [n for n in range(channels)]

        # if channels is list/np array, copy as is
        elif isinstance(channels, (list, np.ndarray)):
            assert all(ch<10 for ch in channels), \
                "If list, channel indices must be less than 10!"
            ch_list = channels.copy()

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        # write .wav file, up to 'n_channels'
        sf.write(wav_filename, self.mic_data[ch_list].T, self.fs,
                 subtype)
        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*


# #############################################################################
# %% Class 'SingleFilePSD'
# #############################################################################

class SingleFilePSD:
    """
    Class to store single-file, multichannel PSD and associated frequency-domain
    information. PSDs are assumed single-sided.
    """

    def __init__(self, filename, psd, freq, fs, Ndft=DEFAULT_NDFT,
                 Noverlap=DEFAULT_NOVERLAP, window=DEFAULT_WINDOW):

        # name of file where PSD data originates from
        self.filename = filename

        # Array of Power Spectral Density values (single sided)
        #   (N_mics, Ndft//2+1)-shape array_like
        self.psd = np.atleast_2d(psd)

        # number of microphone channels
        self.N_ch = self.psd.shape[0]

        # frequency vector (single-sided)
        #   (Ndft//2+1,)-shape array_like
        self.freq = freq

        # sampling frequency
        #   int
        self.fs = fs

        # DFT size
        #   int
        self.Ndft = Ndft

        # Overlap size
        #   int
        self.Noverlap = Noverlap

        # window function (name or samples)
        # str, (Ndft,)-shape array_like
        self.window = window

        # frequency resolution
        #   float
        self.df = self.fs/self.Ndft


    # *************************************************************************
    def calc_broadband_PSD(self, kernel_size=100, units='Hz'):
        """
        Calculates broadband components of multichannel PSD using median
        filtering. This technique removes the contribution of tonal peaks.

        Parameters
        ----------
        kernel_size : int, optional
            Size of median filter kernel. The default is 100 Hz.

        units : {'points', 'Hz'}
            Units for kernel size. Default is 'Hz'.

        Returns
        -------
        None.

        """

        assert units in ['points', 'Hz'], \
            "Unknown input for 'units' - must be 'points' or 'Hz' !"

        # if kernel size is given in Hz, calculate equivalent length in points
        if units == 'Hz':
            kernel_size_Hz = np.copy(kernel_size)
            kernel_size = _round_to_nearest_odd(kernel_size_Hz/self.df)

        self.psd_broadband = np.zeros((self.N_ch, self.Ndft//2+1))

        for ch in range(self.N_ch):
            self.psd_broadband[ch, :] = ss.medfilt(self.psd[ch, :], kernel_size)


    # *************************************************************************
    def find_peaks(self, f_low, f_high, dB_above_broadband=3):
        """
        Find peaks in PSD spectrum within a bandwidth [f_low, f_high]. Peaks
        are not restricted to be harmonics of a fundamental frequenycy.
        Optional arguments are the height above PSD broadband component as
        threshold.

        Parameters
        ----------
        f_low : float
            Low frequency limit, in Hz.

        f_high : float
            High frequency limit, in Hz.

        dB_above_broadband : float, optional
            Minimum peak height above broadband PSD component, in decibels.
            Default value is 3 dB.


        Returns
        -------
        peak_indices : (N_ch, N_peaks)-shape array_like
            Array of indices for all peaks above threshold.

        peak_lims : (N_ch, N_peaks, 2)-shape array_like
            Lower and upper indices determining the width of each peak.
            Defined as the points where the peak in raw PSD crosses the PSD
            broadband component.
        """

        # assert instance has psd broadband defined
        assert hasattr(self, 'psd_broadband'), \
            "Cannot find peaks: PSD instance does not have attribute 'psd_broadband'!"

        gain_above_broadband = 10**(dB_above_broadband/10)

        freq_mask = (self.freq >= f_low) & (self.freq <= f_high)

        # initialize 'peak_indices' with a large size (Ndft/2+1), reduce it later
        self.peak_indices = np.zeros((self.N_ch, self.Ndft//2+1), dtype=int)

        N_peaks = 0

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        for ch in range(self.N_ch):
            height_range = (self.psd_broadband[ch, freq_mask]*gain_above_broadband,
                            None)

            peak_indices_ch, peak_properties = ss.find_peaks(self.psd[ch, freq_mask],
                                                             height=height_range)

            # number of peaks found in this ch
            N_peaks_ch = peak_indices_ch.shape[0]
            self.peak_indices[ch, :N_peaks_ch] = peak_indices_ch

            # largest number of peaks found so far
            N_peaks = np.max([N_peaks, N_peaks_ch])

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
        # change size of 'peak_indices' to largest no. of peaks found
        temp_peaks = np.copy(self.peak_indices[:, :N_peaks])
        self.peak_indices = np.copy(temp_peaks)

        # add initial index of freq_mask to all non-zero entries
        self.peak_indices[self.peak_indices!=0] += np.argwhere(freq_mask)[0, 0]

        # replace zeros with '-1' as flag for 'no peak found'
        self.peak_indices[self.peak_indices==0] = -1

        # find peak limits
        self.peak_lims = self._find_peak_lims(self.peak_indices)

        # *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*

        return self.peak_indices, self.peak_lims


    # *************************************************************************
    def _find_peak_lims(self, peak_indices, radius=20, units='points'):
        """
        For a list of peaks in 'psd', given by 'peak_indices', finds a list
        of lower and upper indices to determine peak widths.

        Parameters
        ----------
        peak_indices : (N_ch, N_peaks,)-shape array_like
            Array containing the peak indices per channel.

        radius : int or float, optional
            Search radius for peak limits. Default is 20 points.

        units : {'points', 'Hz'}, optional
            Units for search radius. Default is 'points'.


        Returns
        -------
        peak_lims : (N_ch, N_peaks, 2)-shaped array_like
            Array containing the indices for lower and upper limits of each
            peak, per channel.
        """

        assert units in ['points', 'Hz'], \
            "Unknown input for 'units' - must be 'points' or 'Hz' !"

        # if kernel size is given in Hz, calculate equivalent length in points
        if units == 'Hz':
            radius_Hz = np.copy(radius)
            radius = _round_to_nearest_odd(radius_Hz/self.df)

        N_data = (self.psd).shape[1]

        N_peaks = peak_indices.shape[1]

        peak_lims = np.zeros((self.N_ch, N_peaks, 2), dtype=np.int64)

        for ch in range(self.N_ch):
            for n_pk, peak_index in enumerate(peak_indices[ch, :]):

                # -.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-
                # if peak index is '-1', write '-1' on peak lims too
                if peak_index == -1:
                    peak_lims[ch, n_pk, :] = -1

                else:
                    # .........................................................
                    # If peak is closer than 'f_radius' to index 0, use index 0
                    # as peak lim
                    if (peak_index - radius)<=0:
                        peak_lims[ch, n_pk, 0] = 0

                    else:
                        # Region *below* 'peak_index' where 'psd' is lower or equal
                        # to 'psd_broadband'
                        cond_lower = (self.psd[ch, peak_index - radius : peak_index]
                                      <= self.psd_broadband[ch, peak_index - radius : peak_index]).nonzero()[0]


                        # if no value found, take lower edge of search radius
                        if cond_lower.size == 0:
                            lower_lim = -radius

                        # If one or more values found, take last element as lower edge of peak
                        else:
                            lower_lim = cond_lower[-1]

                        peak_lims[ch, n_pk, 0] = lower_lim + (peak_index - radius)

                    # .........................................................
                    # If peak is closer than 'f_radius' to 'N_data', use 'N_data'
                    # as peak lim
                    if (peak_index + radius+1) >= N_data:
                        peak_lims[ch, n_pk, 1] = N_data

                    else:
                        # Region *above* 'peak_index' where 'psd' is lower or equal to
                        # 'psd_broadband'
                        cond_upper = (self.psd[ch, peak_index : peak_index + radius + 1]
                                      <= self.psd_broadband[ch, peak_index : peak_index + radius + 1]).nonzero()[0]

                        # if no value found, take upper edge of search radius
                        if cond_upper.size == 0:
                            upper_lim = + radius

                        # If one or more values found, take first element as upper edge of peak
                        else:
                            upper_lim = cond_upper[0]

                        peak_lims[ch, n_pk, 1] = upper_lim + peak_index

                # -.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-
                # check if peak_lims are identical to any previous peak,
                # replace with -1 if so
                if ( peak_lims[ch, n_pk, :] in peak_lims[ch, :n_pk, :]):
                    peak_lims[ch, n_pk, :] = -1
                # -.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-

        return peak_lims


    # *************************************************************************
    def calc_broadband_SPL(self, f_low, f_high):
        """
        Returns array of integrated broadband SPL per channel, in dB re 20 uPa
        RMS, within a frequency range [f_low, f_high].

        Parameters
        ----------
        f_low : float
            Low frequency limit, in Hz.

        f_high : float
            High frequency limit, in Hz.

        Returns
        -------
        broadband_SPL : (N_ch,)-shape array_like
            Integrated broadband SPL per channel, in dB re 20 uPa RMS, within
            the frequency band [f_low, f_high].
        """

        # assert instance has psd broadband defined
        assert hasattr(self, 'psd_broadband'), \
            "Cannot calculate broadband SPL: MutiChannelPSD instance does not have attribute 'psd_broadband'!"

        freq_mask = (self.freq >= f_low) & (self.freq <= f_high)

        integrated_broadband_psd = np.sum(self.psd_broadband[:, freq_mask],
                                          axis=1)*self.df

        self.broadband_SPL = 10*np.log10(integrated_broadband_psd/(P_REF**2))

        return self.broadband_SPL


    # *************************************************************************
    def calc_overall_SPL(self, f_low, f_high):
        """
        Returns integrated overall SPL per channel, in dB re 20 uPa RMS, within
        a frequency range [f_low, f_high].

        Parameters
        ----------
        f_low : float
            Low frequency limit, in Hz.

        f_high : float
            High frequency limit, in Hz.

        Returns
        -------
        overall_SPL : (N_ch,)-shape array_like
            Integrated overall SPL per channel, in dB re 20 uPa RMS, within
            the frequency band [f_low, f_high].
        """

        freq_mask = (self.freq >= f_low) & (self.freq <= f_high)

        integrated_oa_psd = np.sum(self.psd[:, freq_mask], axis=1)*self.df

        self.overall_SPL = 10*np.log10(integrated_oa_psd/(P_REF**2))

        return self.overall_SPL


    # *************************************************************************
    def _calc_peaks_SPL(self):
        """
        Returns array of all peaks' SPL per channel, in dB re 20 uPa RMS.

        Parameters
        ----------
        None

        Returns
        -------
        peaks_SPL : (N_ch, N_peaks)-shape array_like
            Array of integrated peaks' SPL per channel, in dB re 20 uPa RMS.

        Notes
        -----
        Number of tones can vary across channels. If a given peak is not present
        on a channel, its SPL is NaN.
        """

        assert hasattr(self, 'peak_lims'), \
            "Cannot calculate peaks' SPL: SingleFilePSD instance does not have attribute 'peak_lims'!"

        N_peaks = self.peak_indices.shape[1]

        self.peaks_SPL = np.zeros((self.N_ch, N_peaks))

        for ch in range(self.N_ch):
            for n_pk in range(N_peaks):

                # if peak lims is -1 (no peak found), SPL is NaN
                if self.peak_lims[ch, n_pk, 0] == -1:
                    self.peaks_SPL[ch, n_pk] = np.nan

                else:
                    peak_range = np.arange(self.peak_lims[ch, n_pk, 0],
                                           self.peak_lims[ch, n_pk, 1]+1)

                    # subtract broadband content from PSD peak
                    peak_minus_bb = (self.psd[ch, peak_range]
                                     - self.psd_broadband[ch, peak_range])

                    integrated_peak_psd = np.sum(peak_minus_bb)*self.df

                    self.peaks_SPL[ch, n_pk] = 10*np.log10(integrated_peak_psd/(P_REF**2))

        return self.peaks_SPL


    # *************************************************************************
    def calc_tonal_SPL(self):
        """
        Returns the tonal SPL per channel, as the sum of all peaks' SPLs.

        Parameters
        ----------
        None

        Returns
        -------
        tonal_SPL : (N_ch,)-shape array_like
            Array of integrated peaks' SPL per channel, in dB re 20 uPa.
            RMS.

        Notes
        -----
        Must be called after 'find_peaks' method.
        """

        assert hasattr(self, 'peak_lims'),\
            "Cannot calculate tonal SPL: SingleFilePSD instance does not have attribute 'peak_lims'!"

        self.tonal_SPL = np.zeros(self.N_ch)

        self._calc_peaks_SPL()
        peaks_SPL = self.peaks_SPL

        for ch in range(self.N_ch):

            # sum of tones' squared pressures (ignoring NaNs)
            nan_mask = np.isnan(peaks_SPL[ch, :])
            peaks_valid = peaks_SPL[ch, :][~nan_mask]

            sum_bpfs = np.sum(10**(peaks_valid/10))*(P_REF**2)
            self.tonal_SPL[ch] = 10*np.log10(sum_bpfs/(P_REF**2))

        return self.tonal_SPL

