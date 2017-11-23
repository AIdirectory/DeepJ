"""
Preprocesses MIDI files
"""
import math
import numpy as np
import torch

import numpy
import math
import random
from tqdm import tqdm
import multiprocessing

from constants import *
from midi_io import load_midi
from util import *

def load(styles=STYLES):
    """
    Loads all music styles into a list of compositions
    """
    style_seqs = []
    for style in styles:
        # Parallel process all files into a list of music sequences
        style_seq = []
        seq_len_sum = 0

        for f in tqdm(get_all_files([style])):
            try:
                # Pad the sequence by an empty event
                seq = load_midi(f)
                if len(seq) >= SEQ_LEN:
                    style_seq.append(torch.from_numpy(seq).long())
                    seq_len_sum += len(seq)
                else:
                    print('Ignoring {} because it is too short {}.'.format(f, len(seq)))
            except Exception as e:
                print('Unable to load {}'.format(f), e)
        
        style_seqs.append(style_seq)
        print('Loading {} MIDI file(s) with average event count {}'.format(len(style_seq), seq_len_sum / len(style_seq)))
    return style_seqs

def progress_tensor(seq):
    """
    Create vector of progress categories: beginning, middle, end
    """
    step = len(seq) // CATEGORY_LEVEL
    progress = np.zeros((len(seq), CATEGORY_LEVEL))
    for c in range(CATEGORY_LEVEL):
        start = c * step
        end = start + step
        progress[start:end, c] = 1
    # Edge case where if seq len is not cleanly divided by category level and last remainder rows are not assigned a category
    progress[-(len(seq) % CATEGORY_LEVEL):, CATEGORY_LEVEL - 1] = 1
    return torch.FloatTensor(progress)

def process(style_seqs):
    """
    Process data. Takes a list of styles and flattens the data, returning the necessary tags.
    """
    # Flatten into compositions list
    seqs = [s for y in style_seqs for s in y]
    style_tags = torch.LongTensor([s for s, y in enumerate(style_seqs) for x in y])
    progresses = [progress_tensor(s) for s in seqs]
    return seqs, style_tags, progresses

def validation_split(data, split=0.05):
    """
    Splits the data iteration list into training and validation indices
    """
    seqs, style_tags, progresses = data

    # Shuffle sequences randomly
    r = list(range(len(seqs)))
    random.shuffle(r)

    num_val = int(math.ceil(len(r) * split))
    train_indicies = r[:-num_val]
    val_indicies = r[-num_val:]

    assert len(val_indicies) == num_val
    assert len(train_indicies) == len(r) - num_val

    train_seqs = [seqs[i] for i in train_indicies]
    val_seqs = [seqs[i] for i in val_indicies]

    train_style_tags = [style_tags[i] for i in train_indicies]
    val_style_tags = [style_tags[i] for i in val_indicies]

    train_progresses = [progresses[i] for i in train_indicies]
    val_progresses = [progresses[i] for i in val_indicies]
    
    return (train_seqs, train_style_tags, train_progresses), (val_seqs, val_style_tags, val_progresses)

def sampler(data):
    """
    Generates sequences of data.
    """
    seqs, style_tags, progresses = data

    if len(seqs) == 0:
        raise 'Insufficient training data.'

    def sample(seq_len):
        # Pick random sequence
        seq_id = random.randint(0, len(seqs) - 1)

        subseq, start_index, end_index = random_subseq(seqs[seq_id], seq_len)
        progress = progresses[seq_id]

        return (
            gen_to_tensor(augment(subseq)),
            # Need to retain the tensor object. Hence slicing is used.
            torch.LongTensor(style_tags[seq_id:seq_id+1]),
            progress[start_index:end_index]
        )
    return sample

def batcher(sampler):
    """
    Bundles samples into batches
    """
    def batch(batch_size=BATCH_SIZE, seq_len=SEQ_LEN):
        batch = [sampler(seq_len) for i in range(batch_size)]
        return [torch.stack(x) for x in zip(*batch)]
    return batch 

def random_subseq(sequence, seq_len):
    """ Randomly creates a subsequence from the sequence """
    index = random.randint(0, len(sequence) - 1 - seq_len)

    def generator():
        note_ons = set()
        i = 0
        current = index
        
        while i < seq_len:
            if current >= len(sequence):
                # Ran out of events due to skipping
                # Pad with max silence for end of track
                yield TIME_OFFSET + TIME_QUANTIZATION - 1
                i += 1
                current += 1
            else:
                evt = sequence[current]

                if evt < NOTE_OFF_OFFSET + NUM_NOTES:
                    if evt >= NOTE_OFF_OFFSET:
                        # This is a note off
                        note = evt - NOTE_OFF_OFFSET
                        if note not in note_ons:
                            # Ignore note offs before note on
                            current += 1
                            continue
                        else:
                            note_ons.remove(note)
                    else:
                        # This is a note on
                        note_ons.add(evt - NOTE_ON_OFFSET)

                yield evt
                i += 1
                current += 1

    start_index = index
    end_index = index + seq_len
    return generator(), start_index, end_index

def augment(sequence):
    """
    Takes a sequence of events and randomly perform augmentations.
    """
    # Transpose by 4 semitones at most
    transpose = random.randint(-4, 4)

    if transpose == 0:
        return sequence

    # Perform transposition (consider only notes)
    return (evt + transpose if evt < TIME_OFFSET else evt for evt in sequence)
