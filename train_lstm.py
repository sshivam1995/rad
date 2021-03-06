import numpy as np
import torch
import argparse
import os
import math
import gym
import sys
import random
import time
import json
import dmc2gym
import copy

import utils_lstm as utils
from logger import Logger
from video import VideoRecorder

from curl_sac_lstm import RadSacAgent
from torchvision import transforms
from torchvision import utils as tvu
import data_augs as rad

## Training Section
def parse_args():
    parser = argparse.ArgumentParser()
    # environment
    parser.add_argument('--domain_name', default='cartpole')
    parser.add_argument('--task_name', default='swingup')
    parser.add_argument('--pre_transform_image_size', default=100, type=int)

    parser.add_argument('--image_size', default=84, type=int)
    parser.add_argument('--action_repeat', default=1, type=int)
    parser.add_argument('--frame_stack', default=3, type=int)
    # replay buffer
    parser.add_argument('--replay_buffer_capacity', default=800, type=int)
    # train
    parser.add_argument('--agent', default='rad_sac', type=str)
    parser.add_argument('--init_steps', default=10, type=int)
    parser.add_argument('--num_train_steps', default=1000000, type=int)
    parser.add_argument('--batch_size', default=32, type=int)
    parser.add_argument('--hidden_dim', default=1024, type=int)
    # eval
    parser.add_argument('--eval_freq', default=10, type=int)
    parser.add_argument('--num_eval_episodes', default=10, type=int)
    # critic
    parser.add_argument('--critic_lr', default=1e-3, type=float)
    parser.add_argument('--critic_beta', default=0.9, type=float)
    parser.add_argument('--critic_tau', default=0.01, type=float) # try 0.05 or 0.1
    parser.add_argument('--critic_target_update_freq', default=2, type=int) # try to change it to 1 and retain 0.01 above
    # actor
    parser.add_argument('--actor_lr', default=1e-3, type=float)
    parser.add_argument('--actor_beta', default=0.9, type=float)
    parser.add_argument('--actor_log_std_min', default=-10, type=float)
    parser.add_argument('--actor_log_std_max', default=2, type=float)
    parser.add_argument('--actor_update_freq', default=2, type=int)
    # encoder
    parser.add_argument('--encoder_type', default='pixel', type=str)
    parser.add_argument('--encoder_feature_dim', default=50, type=int)
    parser.add_argument('--encoder_lr', default=1e-3, type=float)
    parser.add_argument('--encoder_tau', default=0.05, type=float)
    parser.add_argument('--num_layers', default=4, type=int)
    parser.add_argument('--num_filters', default=32, type=int)
    parser.add_argument('--latent_dim', default=128, type=int)
    # sac
    parser.add_argument('--discount', default=0.99, type=float)
    parser.add_argument('--init_temperature', default=0.1, type=float)
    parser.add_argument('--alpha_lr', default=1e-4, type=float)
    parser.add_argument('--alpha_beta', default=0.5, type=float)
    # testing
    parser.add_argument('--testing', default=False, action='store_true')
    parser.add_argument('--load_step', default=0, type=int)
    parser.add_argument('--train_dir', default='', type=str)
    parser.add_argument('--eval_steps', default=3, type=int)
    parser.add_argument('--save_image', default=False, action='store_true')
    parser.add_argument('--adversarial_iters', default=10, type=int)
    parser.add_argument('--attack_prob', default=0.5, type=float)
    parser.add_argument('--train_data_augs', default='no_aug', type=str)
    # LSTM
    parser.add_argument('--lstm_num_layers', default=1, type=int)
    parser.add_argument('--lstm_dropout', default=0.0, type=float)
    parser.add_argument('--lstm_lookback', default=5, type=int)
    # misc
    parser.add_argument('--seed', default=1, type=int)
    parser.add_argument('--work_dir', default='.', type=str)
    parser.add_argument('--save_tb', default=False, action='store_true')
    parser.add_argument('--save_buffer', default=False, action='store_true')
    parser.add_argument('--save_video', default=False, action='store_true')
    parser.add_argument('--save_model', default=False, action='store_true')
    parser.add_argument('--detach_encoder', default=False, action='store_true')
    # data augs
    parser.add_argument('--data_augs', default='no_aug', type=str)


    parser.add_argument('--log_interval', default=5, type=int)
    args = parser.parse_args()
    return args


def evaluate_val(env, agent, video, num_episodes, L, step, args):
    all_ep_rewards = []

    def run_eval_loop(sample_stochastically=True):
        start_time = time.time()
        prefix = 'stochastic_' if sample_stochastically else ''
        for i in range(num_episodes):
            obs = env.reset()
            video.init(enabled=(i == 0))
            done = False
            episode_reward = 0
            
            obses = obs[None,:]
            while not done:
                # center crop image
                if args.encoder_type == 'pixel' and 'crop' in args.data_augs:
                    obs = utils.center_crop_image(obs,args.image_size)
                if args.encoder_type == 'pixel' and 'translate' in args.data_augs:
                    # first crop the center with pre_image_size
                    obs = utils.center_crop_image(obs, args.pre_transform_image_size)
                    # then translate cropped to center
                    obs = utils.center_translate(obs, args.image_size)
                with utils.eval_mode(agent):
                    if sample_stochastically:
                        action = agent.sample_action(obses[None,:] / 255.)
                    else:
                        action = agent.select_action(obses[None,:] / 255.)
                obs, reward, done, _ = env.step(action)
                obses = np.concatenate((obses, obs[None,:]))
                video.record(env)
                episode_reward += reward

            video.save('%d.mp4' % step)
            L.log('eval/' + prefix + 'episode_reward', episode_reward, step)
            all_ep_rewards.append(episode_reward)
        
        L.log('eval/' + prefix + 'eval_time', time.time()-start_time , step)
        mean_ep_reward = np.mean(all_ep_rewards)
        best_ep_reward = np.max(all_ep_rewards)
        std_ep_reward = np.std(all_ep_rewards)
        L.log('eval/' + prefix + 'mean_episode_reward', mean_ep_reward, step)
        L.log('eval/' + prefix + 'best_episode_reward', best_ep_reward, step)

        filename = args.work_dir + '/' + args.domain_name + '--'+args.task_name + '-' + args.data_augs + '--s' + str(args.seed) + '--eval_scores.npy'
        key = args.domain_name + '-' + args.task_name + '-' + args.data_augs
        try:
            log_data = np.load(filename,allow_pickle=True)
            log_data = log_data.item()
        except:
            log_data = {}
            
        if key not in log_data:
            log_data[key] = {}

        log_data[key][step] = {}
        log_data[key][step]['step'] = step 
        log_data[key][step]['mean_ep_reward'] = mean_ep_reward 
        log_data[key][step]['max_ep_reward'] = best_ep_reward 
        log_data[key][step]['std_ep_reward'] = std_ep_reward 
        log_data[key][step]['env_step'] = step * args.action_repeat

        np.save(filename,log_data)

    run_eval_loop(sample_stochastically=False)
    L.dump(step)


def make_agent(obs_shape, action_shape, args, device):
    if args.agent == 'rad_sac':
        return RadSacAgent(
            obs_shape=obs_shape,
            action_shape=action_shape,
            device=device,
            hidden_dim=args.hidden_dim,
            discount=args.discount,
            init_temperature=args.init_temperature,
            alpha_lr=args.alpha_lr,
            alpha_beta=args.alpha_beta,
            actor_lr=args.actor_lr,
            actor_beta=args.actor_beta,
            actor_log_std_min=args.actor_log_std_min,
            actor_log_std_max=args.actor_log_std_max,
            actor_update_freq=args.actor_update_freq,
            critic_lr=args.critic_lr,
            critic_beta=args.critic_beta,
            critic_tau=args.critic_tau,
            critic_target_update_freq=args.critic_target_update_freq,
            encoder_type=args.encoder_type,
            encoder_feature_dim=args.encoder_feature_dim,
            encoder_lr=args.encoder_lr,
            encoder_tau=args.encoder_tau,
            num_layers=args.num_layers,
            num_filters=args.num_filters,
            log_interval=args.log_interval,
            detach_encoder=args.detach_encoder,
            latent_dim=args.latent_dim,
            data_augs=args.data_augs,
            lstm_num_layers=args.lstm_num_layers,
            lstm_dropout=args.lstm_dropout
        )
    else:
        assert 'agent is not supported: %s' % args.agent

def main():
    args = parse_args()
    if args.seed == -1: 
        args.__dict__["seed"] = np.random.randint(1,1000000)
    utils.set_seed_everywhere(args.seed)

    pre_transform_image_size = args.pre_transform_image_size if 'crop' in args.data_augs else args.image_size
    pre_image_size = args.pre_transform_image_size # record the pre transform image size for translation
    
    env = dmc2gym.make(
        domain_name=args.domain_name,
        task_name=args.task_name,
        seed=args.seed,
        visualize_reward=False,
        from_pixels=(args.encoder_type == 'pixel'),
        height=pre_transform_image_size,
        width=pre_transform_image_size,
        frame_skip=args.action_repeat
    )
 
    env.seed(args.seed)

    # stack several consecutive frames together
    if args.encoder_type == 'pixel':
        env = utils.FrameStack(env, k=args.frame_stack)
    
    # make directory
    data_augs = args.train_data_augs if args.testing else args.data_augs
    env_name = args.domain_name + '-' + args.task_name
    exp_name = env_name + '-im' + str(args.image_size) \
    + '-s' + str(args.seed)  + '-' + data_augs + '-lstm'
    args.work_dir = args.work_dir + '/'  + exp_name

    utils.make_dir(args.work_dir)
    video_dir = utils.make_dir(os.path.join(args.work_dir, 'video'))
    model_dir = utils.make_dir(os.path.join(args.work_dir, 'model'))
    buffer_dir = utils.make_dir(os.path.join(args.work_dir, 'buffer'))

    video = VideoRecorder(video_dir if args.save_video else None)

    with open(os.path.join(args.work_dir, 'args.json'), 'w') as f:
        json.dump(vars(args), f, sort_keys=True, indent=4)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    action_shape = env.action_space.shape
    
    if args.encoder_type == 'pixel':
        obs_shape = (3*args.frame_stack, args.image_size, args.image_size)
        pre_aug_obs_shape = (3*args.frame_stack,pre_transform_image_size,pre_transform_image_size)
    else:
        obs_shape = env.observation_space.shape
        pre_aug_obs_shape = obs_shape

    replay_buffer = utils.ReplayBuffer(
        obs_len = args.lstm_lookback,
        obs_shape=pre_aug_obs_shape,
        action_shape=action_shape,
        capacity=args.replay_buffer_capacity,
        batch_size=args.batch_size,
        device=device,
        image_size=args.image_size,
        pre_image_size=pre_image_size,
    )

    agent = make_agent(
        obs_shape=obs_shape,
        action_shape=action_shape,
        args=args,
        device=device
    )


    L = Logger(args.work_dir, use_tb=args.save_tb)

    episode, episode_reward, done = 0, 0, True
    start_time = time.time()

    if args.testing:
      print('Testing model')
      evaluate_test(env, agent, video, args.num_eval_episodes, L, args)
      return

    for step in range(args.num_train_steps):
        # evaluate agent periodically
        if (step + 1) % args.eval_freq == 0:
            L.log('eval/episode', episode, step)
            evaluate_val(env, agent, video, args.num_eval_episodes, L, step, args)
            if args.save_model:
                agent.save_curl(model_dir, step + 1)
                agent.save(model_dir, step + 1)
            if args.save_buffer:
                replay_buffer.save(buffer_dir)
        
        if done:
            if step > 0:
                if step % args.log_interval == 0:
                    L.log('train/duration', time.time() - start_time, step)
                    L.dump(step)
                start_time = time.time()
            if step % args.log_interval == 0:
                L.log('train/episode_reward', episode_reward, step)

            obs = env.reset()
            done = False
            episode_reward = 0
            episode_step = 0
            episode += 1
            if step % args.log_interval == 0:
                L.log('train/episode', episode, step)
        
        obses = obs[None,:]
        actions = np.empty((0,1))
        lstm_step = 0
        # sample action for data collection
        while lstm_step < args.lstm_lookback:
            if step < args.init_steps:
                action = env.action_space.sample()
            else:
                with utils.eval_mode(agent):
                    action = agent.sample_action(obses[None,:] / 255.)

            next_obs, reward, done, _ = env.step(action)
            obses = np.concatenate((obses, next_obs[None,:]))
            actions = np.concatenate((actions, action[None,:]))
            
            episode_reward += reward
            episode_step += 1
            lstm_step += 1
        
        # run training update
        if step >= args.init_steps:
            agent.update(replay_buffer, L, step)

        # allow infinit bootstrap
        done_bool = 0 if episode_step + 1 == env._max_episode_steps else float(
            done
        )

        replay_buffer.add(obses[:-1], actions, reward, obs[-1], done_bool)

# Testing Functions
def adversarial_obs(agent, obses, actions, iters):
    obses_adv = torch.FloatTensor(obses.copy()).to(agent.device)
    obses_adv = obses_adv[None, :]
    obses_adv = torch.autograd.Variable(obses_adv, requires_grad=True)
    
    actions_adv = torch.FloatTensor(actions.copy()).to(agent.device)
    actions_adv = actions_adv[None, :]

    learning_rate = 0.1
    for i in range(iters):
        obs_adv_grad = agent.actor_obs_grad(obses_adv, actions_adv)
        obs_adv_grad[torch.isnan(obs_adv_grad)] = 0.0
        
        if obs_adv_grad.norm().item() == 0.0:
            break
        
        obs_adv_grad = learning_rate * (obs_adv_grad / obs_adv_grad.norm())

        obses_adv = torch.autograd.Variable(obses_adv, requires_grad=False)
        obses_adv = obses_adv.add(obs_adv_grad).clamp(min=0.0, max=1.0)
        obses_adv = torch.autograd.Variable(obses_adv, requires_grad=True)

    return obses_adv

def save_obs_as_image(obs, fname):
    obs_img = torch.FloatTensor(obs).view(-1, 3, obs.shape[1], obs.shape[2])
    tvu.save_image(obs_img, fname)

def save_images(obs_list, step, args):
    i = random.randint(0, len(obs_list) - 1)
    
    image_dir = utils.make_dir(os.path.join(args.work_dir, 'image'))

    obs_img_name = 'obs_step_' + str(step) + '_' + str(args.attack_prob) + '.png'
    obs_adv_img_name = 'obs_adv_step_' + str(step) + '_' + str(args.attack_prob) + '.png'

    obs_path = os.path.join(image_dir, obs_img_name)
    obs_adv_path = os.path.join(image_dir, obs_adv_img_name)

    save_obs_as_image(obs_list[i][0], obs_path)
    save_obs_as_image(obs_list[i][1], obs_adv_path)

def evaluate_step(env, agent, video, args, num_episodes, L, step, all_ep_rewards):
    start_time = time.time()
    for i in range(num_episodes):
        obs = env.reset()
        video.init(enabled=(i == 0))
        done = False
        episode_reward = 0
        
        obses = obs[None,:] / 255.
        actions = np.empty((0,1))
        
        first_iter = True
        while not done:
            with utils.eval_mode(agent):
                if not(first_iter) and random.random() < args.attack_prob:
                    obses_adv = adversarial_obs(agent, obses, actions, args.adversarial_iters)
                    action = agent.select_action(obses_adv)
                else:
                    action = agent.select_action(obses[None,:])
                    first_iter = False
            
            obs, reward, done, _ = env.step(action)
            if obses.shape[0] == args.lstm_lookback:
                obses = np.concatenate((obses[1:], obs[None,:] / 255.))
                actions = np.concatenate((actions[1:], action[None,:]))
            else:
                obses = np.concatenate((obses, obs[None,:] / 255.))
                actions = np.concatenate((actions, action[None,:]))

            video.record(env)
            episode_reward += reward
        
        video.save('%d.mp4' % step)
        L.log('eval/' + 'episode_reward', episode_reward, step)
        all_ep_rewards.append(episode_reward)

    return time.time() - start_time
    
def evaluate_test(env, agent, video, num_episodes, L, args):
    all_ep_rewards = []
    model_dir = os.path.join(args.train_dir, 'model')
    agent.load(model_dir, args.load_step)
    
    model_name = model_dir.split('/')[-2]
    filename = args.work_dir + '/' + model_name + '--eval_scores.npy'
    key = model_name
    
    for step in range(args.eval_steps):
        end_time = evaluate_step(env, agent, video, args, num_episodes, L, step, all_ep_rewards)
        
        L.log('eval/' + 'eval_time', end_time , step)
        mean_ep_reward = np.mean(all_ep_rewards)
        best_ep_reward = np.max(all_ep_rewards)
        std_ep_reward = np.std(all_ep_rewards)
        L.log('eval/' + 'mean_episode_reward', mean_ep_reward, step)
        L.log('eval/' + 'best_episode_reward', best_ep_reward, step)

        try:
            log_data = np.load(filename,allow_pickle=True)
            log_data = log_data.item()
        except:
            log_data = {}
            
        if key not in log_data:
            log_data[key] = {}

        log_data[key][step] = {}
        log_data[key][step]['step'] = step 
        log_data[key][step]['mean_ep_reward'] = mean_ep_reward 
        log_data[key][step]['max_ep_reward'] = best_ep_reward 
        log_data[key][step]['std_ep_reward'] = std_ep_reward
        log_data[key][step]['env_step'] = step * args.action_repeat

        np.save(filename,log_data)
        L.dump(step)

if __name__ == '__main__':
    torch.multiprocessing.set_start_method('spawn')
    main()

