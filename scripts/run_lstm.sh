CUDA_VISIBLE_DEVICES=0 python train_lstm.py \
    --domain_name cartpole \
    --task_name swingup \
    --encoder_type pixel --work_dir ./tmp \
    --action_repeat 8 --num_eval_episodes 10 \
    --pre_transform_image_size 100 --image_size 84 \
    --agent rad_sac --frame_stack 3 --data_augs no_aug \
    --seed 94 --critic_lr 1.5e-5 --actor_lr 1.5e-5 \
    --eval_freq 100 --num_train_steps 30000 --batch_size 128 \
    --lstm_num_layers 1 --lstm_dropout 0.0 --lstm_lookback 5 \
    --save_model \
