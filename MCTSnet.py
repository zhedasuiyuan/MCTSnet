import numpy as np
import torch
from tqdm import tqdm
import matplotlib.pyplot as plt

from copy import copy

from IPython.core.debugger import set_trace
import config

import random
from random import shuffle

import hashlib

import pickle
import sys

import utils
import model_utils

from AlphaZero import AlphaZero

np.seterr(all="raise")

class MCTSnet:
    def __init__(self,
                 actions,
                 get_legal_actions,
                 transition_and_evaluate,
                 cuda=torch.cuda.is_available(),
                 best=False):
        utils.create_folders()
        self.has_cuda = cuda

        self.actions = actions
        self.get_legal_actions = get_legal_actions
        self.transition_and_evaluate = transition_and_evaluate

        self.new = model_utils.load_model()
        self.best = model_utils.load_model()
        
        if self.has_cuda: 
            self.new = self.new.cuda()
            self.best = self.best.cuda()

        self.az = AlphaZero()

    def self_play(self, root_state, best_only=True, num_sims=30, num_episodes=20, deterministic=False):
        self.best.eval()
        self.new.eval()

        if best_only:
            order = [self.best, self.best]
            name_order = ["best", "new"]
        else:
            if np.random.uniform() > .5:
                order = [self.best, self.new]
                name_order = ["best", "new"]
            else:
                order = [self.new, self.best]
                name_order = ["new", "best"]

        az = self.az

        game_over = False
        curr_player = 0
        state_np = np.array(root_state)
        state = self.convert_to_torch(root_state).unsqueeze(0)

        scoreboard = {
            "new": 0
            , "best": 0
        }

        memories = []
        for _ in range(num_episodes):
            az.reset()
            if deterministic:
                az.T = 0
            while not game_over:
                sim_state = state.clone() 
                sim_state_np = np.array(state_np)

                for _ in range(num_sims):
                    net = order[curr_player]

                    curr_player += 1
                    curr_player = curr_player % 2

                    sim_state_np, result, sim_over = az.select(sim_state_np, self.transition_and_evaluate)
                    sim_state = self.convert_to_torch(sim_state_np).unsqueeze(0)

                    policy, value = net(sim_state)
                    policy = policy.squeeze().detach()
                    value = value.detach().item()

                    if result is not None:
                        value = result

                    corrected_policy = self.correct_policy(policy, sim_state_np)

                    if not sim_over:
                        az.expand(corrected_policy)

                    az.backup(value)

                action, search_probas = az.select_real()

                memories.append({
                    "state": state,
                    "search_probas": search_probas,
                    "curr_player": curr_player
                })

                state_np, result, game_over = self.transition_and_evaluate(state_np, action)
                state = self.convert_to_torch(state_np)

            if result == -1:
                player = (curr_player + 1) % 2
            else:
                player = curr_player

            scoreboard[name_order[player]] += 1

            for memory in memories:
                if memory["curr_player"] != curr_player:
                    result *= -1
                memory["result"] = result

        if not best_only:
            print(f"Best Wins: {scoreboard['best']}, Challenger Wins: {scoreboard['new']}")

        return memories, scoreboard

    def correct_policy(self, policy, state):
        # state = np.reshape(state, newshape=tuple(state.shape[1:]))
        mask = torch.zeros_like(policy)
        legal_actions = self.get_legal_actions(state[:2])
        mask[legal_actions] = 1
        policy = policy * mask

        pol_sum = (policy.sum() * 1.0)

        if pol_sum != 0:
            policy = policy / pol_sum

        policy = policy.detach().numpy()

        return policy

    def convert_to_torch(self, state):
        state = torch.tensor(state)
        if self.has_cuda:
            state = state.cuda()

        return state

    def tournament(self, root_state, num_sims, num_episodes):
        _, scoreboard = self.self_play(root_state, best_only=False, num_sims=num_sims, num_episodes=num_episodes, deterministic=True)

        if scoreboard["new"] > scoreboard["best"]*config.SCORING_THRESHOLD:
            model_utils.save_model(self.new)
            self.best = model_utils.load_model()
        elif scoreboard["new"]*config.SCORING_THRESHOLD < scoreboard["best"]:
            self.new = model_utils.load_model()