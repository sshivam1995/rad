CUDA_VISIBLE_DEVICES=0 python adversarial_test.py \
    --domain_name cartpole \
    --task_name swingup \
    --encoder_type pixel --work_dir ./test \
    --action_repeat 8 --num_eval_episodes 1 \
    --pre_transform_image_size 100 --image_size 84 \
    --agent rad_sac --frame_stack 3 --seed 54 \
    --testing --save_video --save_image --load_step=$1 \
    --adversarial_iters 50 --attack_prob 0.25 \
    --train_data_augs flip --eval_steps 1 \
    --train_dir ./tmp/cartpole-swingup-im84-s23-flip \