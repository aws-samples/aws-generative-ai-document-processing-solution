#!/bin/bash

# Create the directory
mkdir -p ~/environment/sharplayer/nodejs

# Change directory
cd ~/environment/sharplayer/nodejs

# Initialize the NPM project
npm init -y

# Install the 'sharp' package
npm install --arch=x64 --platform=linux sharp

# Go back to the parent directory
cd ..

# Create a zip file of the 'sharplayer' directory
zip -r sharplayer.zip .

# Copy the 'sharplayer.zip' file
cp sharplayer.zip ~/environment/aws-generative-ai-document-processing-solution/deploy_code/multipagepdfa2i_imageresize/