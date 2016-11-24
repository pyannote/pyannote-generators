#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2016 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr


import random
import numpy as np

from pyannote.core import Segment
from pyannote.core import Timeline
from pyannote.core import Annotation
from pyannote.core import SlidingWindow
from pyannote.core import PYANNOTE_SEGMENT
from pyannote.core import PYANNOTE_TRACK
from pyannote.core import PYANNOTE_LABEL


def random_segment(segments, weighted=False):
    """Generate segment with probability proportional to its duration"""

    p = None
    if weighted:
        total = float(sum(s.duration for s in segments))
        p = [s.duration / total for s in segments]

    n_segments = len(segments)
    while True:
        i = np.random.choice(n_segments, p=p)
        yield segments[i]


def random_subsegment(segment, duration, min_duration=None):
    """Pick a subsegment at random

    Parameters
    ----------
    segment : Segment
    duration : float
        Duration of random subsegment
    min_duration : float, optional
        When provided, choose segment duration at random between `min_duration`
        and `duration` (instead of fixed `duration`).
    """
    if min_duration is None:
        while True:
            # draw start time from [segment.start, segment.end - duration]
            t = segment.start + random.random() * (segment.duration - duration)
            yield Segment(t, t + duration)

    else:
        # make sure max duration is smaller than actual segment duration
        max_duration = min(segment.duration, duration)

        while True:
            # draw duration from [min_duration, max_duration] interval
            rnd_duration = min_duration + random.random() * (max_duration - min_duration)

            # draw start from [segment.start, segment.end - rnd_duration] interval
            t = segment.start + random.random() * (segment.duration - rnd_duration)
            yield Segment(t, t + rnd_duration)

def remove_short_segment(timeline, shorter_than):
    return Timeline([s for s in timeline if s.duration > shorter_than])


class SlidingSegments(object):
    """Sliding segment generator

    Parameters
    ----------
    duration: float, optional
    step: float, optional
        Duration and step of sliding window (in seconds).
        Default to 3.2 and 0.8.
    min_duration: float, optional
        When provided, will do its best to yield segments of length `duration`,
        but shortest segments are also permitted (as long as they are longer
        than `min_duration`).
    source: {'annotated', 'coverage', 'annotation', 'wav'}, optional.
        Defaults to 'annotation'
    """

    def __init__(self, duration=3.2, step=0.8,
                 min_duration=None, source='annotation'):
        super(SlidingSegments, self).__init__()

        self.duration = duration

        self.variable_length_ = min_duration is not None
        if self.variable_length_:
            self.min_duration = min_duration
        else:
            self.min_duration = duration

        self.step = step
        self.source = source

    def signature(self):

        if self.variable_length_:
            return {'type': PYANNOTE_SEGMENT,
                    'min_duration': self.min_duration,
                    'max_duration': self.duration}
        else:
            return {'type': PYANNOTE_SEGMENT,
                    'duration': self.duration}

    def from_file(self, current_file):

        if self.source == 'annotated':
            source = current_file['annotated']

        elif self.source == 'annotation':
            source = current_file['annotation']

        elif self.source == 'coverage':
            source = current_file['annotation'].get_timeline().coverage()

        elif self.source == 'wav':
            from pyannote.audio.features.utils import get_wav_duration
            wav = current_file['medium']['wav']
            source = get_wav_duration(wav)

        else:
            raise ValueError('source must be one of "annotated", "annotation", "coverage" or "wav"')

        for segment in self.iter_segments(source):
            yield segment

    def iter_segments(self, source):
        """
        Parameters
        ----------
        source : float, Segment, Timeline or Annotation
            If `float`, yield running segments within [0, source).
            If `Segment`, yield running segments within this segment.
            If `Timeline`, yield running segments within this timeline.
            If `Annotation`, yield running segments within its timeline.
        """

        if isinstance(source, Annotation):
            segments = source.get_timeline()

        elif isinstance(source, Timeline):
            segments = source

        elif isinstance(source, Segment):
            segments = [source]

        elif isinstance(source, (int, float)):
            if not self.duration > 0:
                raise ValueError('Duration must be strictly positive.')
            segments = [Segment(0, source)]

        else:
            raise TypeError(
                'source must be float, Segment, Timeline or Annotation')

        for segment in segments:

            # skip segments that are too short
            if segment.duration < self.min_duration:
                continue

            # yield segments shorter than duration
            # when variable length segments are allowed
            elif segment.duration < self.duration:
                if self.variable_length_:
                    yield segment

            # yield sliding segments within current track
            else:
                window = SlidingWindow(
                    duration=self.duration, step=self.step,
                    start=segment.start, end=segment.end)

                for s in window:

                    # if current window is fully contained by segment
                    if s in segment:
                        yield s

                    # if it is not but variable length segments are allowed
                    elif self.variable_length_:
                        candidate = s & segment
                        if candidate.duration >= self.min_duration:
                            yield candidate
                        break


class TwinSlidingSegments(SlidingSegments):

    def __init__(self, duration=3.2, step=0.8, gap=0.0):
        super(TwinSlidingSegments, self).__init__(
            duration=duration, step=step, source='wav')
        self.gap = gap

    def signature(self):
        return (
            {'type': 'timestamp'},
            {'type': PYANNOTE_SEGMENT, 'duration': self.duration},
            {'type': PYANNOTE_SEGMENT, 'duration': self.duration}
        )

    def from_file(self, current_file):
        from pyannote.audio.features.utils import get_wav_duration

        wav = current_file['medium']['wav']
        duration = get_wav_duration(wav)

        for left in self.iter_segments(duration):
            right = Segment(left.end + self.gap,
                            left.end + self.duration + self.gap)
            if right.end < duration:
                t = .5 * (left.end + right.start)
                yield t, left, right


class SlidingLabeledSegments(object):
    """(segment, label) tuple generator

    Yields segment using a sliding window over the coverage of the reference.
    Heterogeneous segments (i.e. containing more than one label) are skipped.

    Parameters
    ----------
    duration: float, optional
    step: float, optional
        Duration and step of sliding window (in seconds).
        Default to 3.2 and 0.8.
    min_duration: float, optional
        When provided, will do its best to yield segments of length `duration`,
        but shortest segments are also permitted (as long as they are longer
        than `min_duration`).
    """

    def __init__(self, duration=3.2, step=0.8,
                 min_duration=None, source='annotation'):
        super(SlidingLabeledSegments, self).__init__()

        self.duration = duration

        self.variable_length_ = min_duration is not None
        if self.variable_length_:
            self.min_duration = min_duration
        else:
            self.min_duration = duration

        self.step = step
        self.source = source

    def signature(self):

        if self.variable_length_:
            return ({'type': PYANNOTE_SEGMENT,
                     'min_duration': self.min_duration,
                     'max_duration': self.duration},
                    {'type': PYANNOTE_LABEL})
        else:
            return ({'type': PYANNOTE_SEGMENT, 'duration': self.duration},
                    {'type': PYANNOTE_LABEL})

    def from_file(self, current_file):

        annotation = current_file[self.source]
        if not isinstance(annotation, Annotation):
            raise NotImplementedError('source must be an Annotation instance.')

        for segment, label in self.iter_segments(annotation):
            yield segment, label

    def iter_segments(self, from_annotation):

        for segment, _, label in from_annotation.itertracks(label=True):

            # skip segments that are too short
            if segment.duration < self.min_duration:
                continue

            # yield segments shorter than duration
            # when variable length segments are allowed
            elif segment.duration < self.duration:
                if self.variable_length_:
                    yield (segment, label)

            # yield sliding segments within current track
            else:
                window = SlidingWindow(
                    duration=self.duration, step=self.step,
                    start=segment.start, end=segment.end)

                for s in window:

                    # if current window is fully contained by segment
                    if s in segment:
                        yield (s, label)

                    # if it is not but variable length segments are allowed
                    elif self.variable_length_:
                        candidate = s & segment
                        if candidate.duration >= self.min_duration:
                            yield (candidate, label)
                        break


class RandomLabeledSegments(object):
    """(segment, label) tuple generator

    Generate variable-duration random subsegments of original segments.
    The number of subsegments is proportional to the duration of each segment.

    Parameters
    ----------
    min_duration : float, optional
        Defaults to 1.
    max_duration: float, optional
        Defaults to 5.
    """

    def __init__(self, min_duration=1., max_duration=5):
        super(RandomLabeledSegments, self).__init__()
        self.min_duration = min_duration
        self.max_duration = max_duration

    def signature(self):
        return (
            {'type': PYANNOTE_SEGMENT,
             'min_duration': self.min_duration,
             'max_duration': self.max_duration},
            {'type': PYANNOTE_LABEL}
        )

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for segment in self.iter_segments(annotation):
            yield segment

    def iter_segments(self, from_annotation):
        """
        Parameters
        ----------
        from_annotation : Annotation

        Returns
        -------
        segment
        label

        """

        for segment, _, label in from_annotation.itertracks(label=True):

            # no need to continue if segment is shorter than minimum duration
            duration = segment.duration
            if duration < self.min_duration:
                continue

            # initialize random subsegment generator
            generator = random_subsegment(segment,
                                          self.max_duration,
                                          min_duration=self.min_duration)

            # number of subsegments is proportional
            # to the duration of the original segment
            n_subsegments = int(np.ceil(duration / self.min_duration))

            # actual generate random subsegments
            for _ in range(n_subsegments):
                s = next(generator)
                yield (s, label)


class RandomSegments(object):
    """Infinitie random segment generator

    Parameters
    ----------
    duration: float, optional
        When provided, yield (random) subsegments with this `duration`.
        Defaults to yielding full segments.
    weighted: boolean, optional
        When True, probability of generating a segment is proportional to its
        duration.
    """
    def __init__(self, duration=0., weighted=False):
        super(RandomSegments, self).__init__()
        self.duration = duration
        self.weighted = weighted

    def signature(self):
        return {'type': PYANNOTE_SEGMENT, 'duration': self.duration}

    def pick(self, segment):
        """Pick a subsegment at random"""
        t = segment.start + random.random() * (segment.duration - self.duration)
        return Segment(t, t + self.duration)

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for segment in self.iter_segments(annotation):
            yield segment

    def iter_segments(self, source):
        """
        Parameters
        ----------
        source : float, Segment, Timeline or Annotation
            If `float`, yield random segments within [0, source).
            If `Segment`, yield random segments within this segment.
            If `Timeline`, yield random segments within this timeline.
            If `Annotation`, yield random segments within its timeline.
        """

        if isinstance(source, Annotation):
            segments = source.get_timeline()

        elif isinstance(source, Timeline):
            segments = source

        elif isinstance(source, Segment):
            segments = [source]

        elif isinstance(source, (int, float)):
            if not self.duration > 0:
                raise ValueError('Duration must be strictly positive.')
            segments = [Segment(0, duration)]

        else:
            raise TypeError(
                'source must be float, Segment, Timeline or Annotation')

        segments = [segment for segment in segments
                    if segment.duration > self.duration]

        if not segments:
            raise ValueError(
                'Source must contain at least one segment longer '
                'than requested duration.')

        for segment in random_segment(segments, weighted=self.weighted):
            if self.duration:
                if segment.duration < self.duration:
                    continue
                yield next(random_subsegment(segment, self.duration))
            else:
                yield segment


class RandomSegmentsPerLabel(object):
    """Labeled segments generator

    Parameters
    ----------
    per_label: int, optional
        Number of consecutive segments yielded with the same label
        before switching to another label.
    duration: float, optional
        When provided, yield (random) subsegments with this `duration`.
        Defaults to yielding full segments.
    yield_label: boolean, optional
        When True, yield triplets of (segment, label) pairs
        Defaults to yielding segments.
    """

    def __init__(self, per_label=40, duration=0.0, yield_label=False):
        super(RandomSegmentsPerLabel, self).__init__()
        self.per_label = per_label
        self.duration = duration
        self.yield_label = yield_label

    def signature(self):
        if self.yield_label:
            return (
                {'type': PYANNOTE_SEGMENT, 'duration': self.duration},
                {'type': PYANNOTE_LABEL}
            )
        return {'type': PYANNOTE_SEGMENT, 'duration': self.duration}

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for segment in self.iter_segments(annotation):
            yield segment

    def iter_segments(self, from_annotation):
        """Yield segments

        Parameters
        ----------
        from_annotation : Annotation
            Annotation from which segments are obtained.
        """

        labels = from_annotation.labels()
        random_segments = RandomSegments(duration=self.duration, weighted=True)
        for label in labels:
            timeline = from_annotation.label_timeline(label)
            if self.duration > 0:
                timeline = remove_short_segment(timeline, self.duration)
                if not timeline:
                    continue
            segments = random_segments.iter_segments(timeline)
            for s, segment in enumerate(segments):
                if s == self.per_label:
                    break
                yield (segment, label) if self.yield_label else segment


class RandomTracks(object):
    """(segment, track) tuple generator

    Parameters
    ----------
    yield_label: boolean, optional
        When True, yield (segment, track, label) tuples.
        Defaults to yielding (segment, track) tuples.
    """

    def __init__(self, yield_label=False):
        super(RandomTracks, self).__init__()
        self.yield_label = yield_label

    def signature(self):
        signature = [
            {'type': PYANNOTE_SEGMENT, 'duration': 0.0},
            {'type': PYANNOTE_TRACK}
        ]
        if self.yield_label:
            signature.append({'type': PYANNOTE_LABEL})
        return signature

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for track in self.iter_tracks(reference):
            yield track

    def iter_tracks(self, from_annotation):
        """Yield (segment, track) tuples

        Parameters
        ----------
        from_annotation : Annotation
            Annotation from which tracks are obtained.
        """
        segments = from_annotation.get_timeline()
        n_segments = len(segments)
        while True:
            index = random.randrange(n_segments)
            segment = segments[index]
            track = random.choice(list(from_annotation.get_tracks(segment)))
            if self.yield_label:
                label = from_annotation[segment, track]
                yield segment, track, label
            else:
                yield segment, track


class RandomTrackTriplets(object):
    """(anchor, positive, negative) track triplets generator

    Parameters
    ----------
    per_label: int, optional
        Number of consecutive triplets yielded with the same anchor label
        before switching to another label.
    yield_label: boolean, optional
        When True, yield triplets of (segment, track, label) tuples.
        Defaults to yielding triplets of (segment, track) tuples.
        Useful for logging which labels are more difficult to discriminate.
    """

    def __init__(self, per_label=40, yield_label=False):
        super(RandomTrackTriplets, self).__init__()
        self.per_label = per_label
        self.yield_label = yield_label

    def signature(self):
        return [RandomTracks(yield_label=self.yield_label).signature()] * 3

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for triplet in self.iter_triplets(annotation):
            yield triplet

    def iter_triplets(self, from_annotation):
        """Yield (anchor, positive, negative) triplets of tracks

        Parameters
        ----------
        from_annotation : Annotation
            Annotation from which triplets are obtained.
        """
        for label in from_annotation.labels():

            p = RandomTracks(yield_label=self.yield_label)
            positives = p.iter_tracks(from_annotation.subset([label]))

            n = RandomTracks(yield_label=self.yield_label)
            negatives = n.iter_tracks(from_annotation.subset([label], invert=True))

            for _ in range(self.per_label):
                try:
                    anchor = next(positives)
                    positive = next(positives)
                    negative = next(negatives)
                except StopIteration as e:
                    break
                yield anchor, positive, negative


class RandomSegmentTriplets(object):
    """(anchor, positive, negative) segment triplets generator

    Parameters
    ----------
    duration: float, optional
        When provided, yield (random) subsegments with this `duration`.
        Defaults to yielding full segments.
    per_label: int, optional
        Number of consecutive triplets yielded with the same anchor label
        before switching to another label.
    yield_label: boolean, optional
        When True, yield triplets of (segment, label) tuples.
        Default to yielding segment triplets.
        Useful for logging which labels are more difficult to discriminate.
    """

    def __init__(self, duration=0., per_label=40, yield_label=False):
        super(RandomSegmentTriplets, self).__init__()
        self.duration = duration
        self.per_label = per_label
        self.yield_label = yield_label

    def signature(self):
        if self.yield_label:
            return 3 * [{'type': PYANNOTE_SEGMENT, 'duration': self.duration},
                        {'type': 'label'}]
        else:
            return 3 * [{'type': PYANNOTE_SEGMENT, 'duration': self.duration}]

    def pick(self, segment):
        """Pick a subsegment at random"""
        t = segment.start + random.random() * (segment.duration - self.duration)
        return Segment(t, t + self.duration)

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for triplet in self.iter_triplets(annotation):
            yield triplet

    def iter_triplets(self, from_annotation):
        """Yield (anchor, positive, negative) segment triplets

        Parameters
        ----------
        from_annotation : Annotation
            Annotation from which triplets are obtained.
        """

        t = RandomTrackTriplets(per_label=self.per_label,
                                yield_label=self.yield_label)

        annotation = Annotation(uri=from_annotation.uri,
                                modality=from_annotation.modality)
        for segment, track, label in from_annotation.itertracks(label=True):
            if segment.duration < self.duration:
                continue
            annotation[segment, track] = label

        if len(annotation.labels()) < 2:
            return

        triplets = t.iter_triplets(annotation)

        for triplet in triplets:

            a, p, n = [item[0] for item in triplet]

            if self.duration:
                a, p, n = [self.pick(s) for s in (a, p, n)]

            if self.yield_label:
                a_, p_, n_ = [item[2] for item in triplet]
                yield (a, a_), (p, p_), (n, n_)
            else:
                yield a, p, n


class RandomSegmentPairs(object):
    """((query, returned), relevance) generator

    where `query` and `returned` are segments and `relevance` is boolean
    indicates whether `returned` has the same label as `query`.

    Parameters
    ----------
    duration: float, optional
        When provided, yield (random) subsegments with this `duration`.
        Defaults to yielding full segments.
    per_label: int, optional
        Number of consecutive relevant and irrelevant pairs yielded with the
        same query label before switching to another label.
    yield_label: boolean, optional
        When True, yield triplets of (segment, label) tuples.
        Default to yielding segment triplets.
        Useful for logging which labels are more difficult to discriminate.

    """
    def __init__(self, duration=0., per_label=40, yield_label=False):
        super(RandomSegmentPairs, self).__init__()
        self.duration = duration
        self.per_label = per_label
        self.yield_label = yield_label

    def signature(self):
        t = RandomSegmentTriplets(duration=self.duration,
                                  per_label=self.per_label,
                                  yield_label=self.yield_label)
        signature = t.signature()
        return [(signature[0], signature[0]), {'type': 'boolean'}]

    def from_file(self, current_file):
        annotation = current_file['annotation']
        for pair in self.iter_pairs(annotation):
            yield pair

    def iter_pairs(self, from_annotation):
        """Yield ((query, returned), relevance)

        Parameters
        ----------
        from_annotation : Annotation
            Annotation from which triplets are obtained.
        """

        t = RandomSegmentTriplets(duration=self.duration,
                                  per_label=self.per_label,
                                  yield_label=self.yield_label)
        triplets = t.iter_triplets(from_annotation)

        for query, positive, negative in triplets:
            yield [(query, positive), True]
            yield [(query, negative), False]
