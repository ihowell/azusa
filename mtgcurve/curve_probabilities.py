from collections import namedtuple
import itertools
import copy

from terminaltables import AsciiTable
import numpy as np

from .mana_producers import PRODUCERS

class defaultdict(dict):
    def __init__(self, factory):
        self.factory=factory
    def __missing__(self, key):
        self[key] = self.factory(key)
        return self[key]

State = namedtuple('State', [
    'num_commanders',
    'turn_number',
    'num_lands_in_play',
    'num_lands_in_hand',
    'num_lands_in_library',
    'mana_producers_in_play',
    'mana_producers_in_hand',
    'mana_producers_in_library',
    'num_other_cards_in_hand',
])


def log_choose(a, b):
    return np.log(np.math.factorial(a) + 0.0) - \
        np.log(np.math.factorial(b) + 0.0) - np.log(np.math.factorial(a - b) + 0.0)


def cards_in_hand(state):
    return state.num_lands_in_hand + len(state.mana_producers_in_hand) + state.num_other_cards_in_hand

def cards_in_library(state):
    return 100 - state.num_commanders - state.num_lands_in_play - len(
        state.mana_producers_in_play) - state.num_lands_in_hand - len(
            state.mana_producers_in_hand) - state.num_other_cards_in_hand

def start_turn(state):
    turn_number = state.turn_number + 1
    possibilities = []

    # Draw a land
    prob = state.num_lands_in_library / cards_in_library(state)
    new_state = state._replace(
        turn_number=turn_number,
        num_lands_in_library=state.num_lands_in_library-1,
        num_lands_in_hand=state.num_lands_in_hand+1)
    possibilities.append((new_state, prob))

    # Draw a mana producer
    for i, producer in enumerate(state.mana_producers_in_library):
        prob = 1. / cards_in_library(state)
        new_state = copy.deepcopy(state)
        new_state = new_state._replace(turn_number=turn_number)
        producer = new_state.mana_producers_in_library.pop(i)
        new_state.mana_producers_in_hand.append(producer)
        possibilities.append((new_state, prob))

    # Draw any other card
    prob = (cards_in_library(state) - state.num_lands_in_library -
            len(state.mana_producers_in_library)) / cards_in_library(state)
    new_state = state._replace(
        turn_number=turn_number,
        num_other_cards_in_hand=state.num_other_cards_in_hand+1)
    possibilities.append((new_state, prob))

    return possibilities

def play_turn(state):
    remaining_mana = state.num_lands_in_play

    for mana_producer in state.mana_producers_in_play:
        remaining_mana += mana_producer.payoff - mana_producer.input_cost

    updates = defaultdict(lambda key: copy.copy(getattr(state, key)))

    if state.num_lands_in_hand > 0:
        updates['num_lands_in_hand'] -= 1
        updates['num_lands_in_play'] += 1
        remaining_mana += 1

    fast_producers = [m for m in state.mana_producers_in_hand if m.payoff > m.cmc + m.input_cost]
    for mana_producer in fast_producers:
        i = updates['mana_producers_in_hand'].index(mana_producer)
        updates['mana_producers_in_hand'].pop(i)
        remaining_mana -= mana_producer.cmc
        if remaining_mana >= mana_producer.input_cost:
            remaining_mana += mana_producer.payoff - mana_producer.input_cost

    mana_on_turn = remaining_mana

    producers_played = []
    for producer in state.mana_producers_in_hand:
        if producer.cmc <= remaining_mana:
            producers_played.append(producer)
            updates['mana_producers_in_play'].append(producer)
            remaining_mana -= producer.cmc
            if remaining_mana >= producer.input_cost:
                remaining_mana += producer.payoff - producer.input_cost

    if len(producers_played) > 0:
        updates['mana_producers_in_hand'] = [p for p in state.mana_producers_in_hand if p not in producers_played]

    state = state._replace(**updates)

    return state, mana_on_turn

def calculate_cmc_probs(num_commanders, mana_producers, num_lands, max_turns=7, max_mana=7):
    def starting_hand_generator():
        log_hand_combinations = log_choose(100 - num_commanders, 7)
        for num_lands_in_hand in range(min(num_lands, 7)):
            log_land_comb = log_choose(num_lands, num_lands_in_hand)

            for num_producers_in_hand in range(min(len(mana_producers) + 1, 8 - num_lands_in_hand)):
                log_prod_comb = np.log(np.math.factorial(num_producers_in_hand))
                log_other_comb = log_choose(100 - num_commanders - num_lands - len(mana_producers),
                                            7 - num_lands_in_hand - num_producers_in_hand)
                log_prob = log_land_comb + log_prod_comb + log_other_comb - log_hand_combinations
                prob = np.exp(log_prob)
                for producers in itertools.combinations(mana_producers, num_producers_in_hand):
                    yield (num_lands_in_hand, list(producers), 7 - num_lands_in_hand - num_producers_in_hand, prob)

    prob_table = np.zeros((max_turns+1, max_mana + 1), dtype=np.double)

    def calculate_subtree_probs(state, initial_prob):
        state, mana = play_turn(state)
        prob_table[state.turn_number, min(mana, max_mana)] += initial_prob
        if state.turn_number >= max_turns:
            return

        possible_states = start_turn(state)
        for child_state, prob in possible_states:
            calculate_subtree_probs(child_state, initial_prob * prob)

    for num_lands_in_hand, mana_producers_in_hand, num_other_cards_in_hand, initial_prob in starting_hand_generator():
        mana_producers_in_library = [m for m in mana_producers if m not in mana_producers_in_hand]
        state = State(num_commanders=num_commanders,
                      turn_number=0,
                      num_lands_in_play=0,
                      num_lands_in_hand=num_lands_in_hand,
                      num_lands_in_library=num_lands - num_lands_in_hand,
                      mana_producers_in_play=[],
                      mana_producers_in_hand=mana_producers_in_hand,
                      mana_producers_in_library=mana_producers_in_library,
                      num_other_cards_in_hand=num_other_cards_in_hand)
        for child_state, prob in start_turn(state):
            calculate_subtree_probs(child_state, initial_prob * prob)
    return prob_table

def display_prob_table(prob_table):
    max_turns = prob_table.shape[0] - 1
    max_mana = prob_table.shape[1] - 1
    table_data = [[''] + [f'Mana {i}' for i in range(max_mana + 1)]]
    for turn in range(1, max_turns + 1):
        row = [f'Turn {turn}']
        for mana in range(max_mana + 1):
            row.append(f'{prob_table[turn][mana]*100:.2f}%')
        table_data.append(row)
    table = AsciiTable(table_data)
    print(table.table)

def main():
    mana_rock_names = [
        'Mana Crypt',
        'Sol Ring',
        'Arcane Signet',
        'Mind Stone',
        'Orzhov Signet',
        'Golgari Signet',
        'Selesnya Signet',
    ]

    mana_rocks = [PRODUCERS[name.lower()] for name in mana_rock_names]

    max_turns = 7
    max_mana = 7

    prob_table = calculate_cmc_probs(1, [], 36)
    display_prob_table(prob_table)



if __name__ == '__main__':
    main()
