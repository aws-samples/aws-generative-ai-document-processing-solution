#!/bin/bash

# Create the necessary directories
mkdir -p ~/environment/sharplayer/nodejs
# Navigate to the newly created directory 
cd ~/environment/sharplayer/nodejs
# Initialize a new npm project with default settings 
npm init -y
# Install the 'sharp' package with specific architecture and platform
npm install --arch=x64 --platform=linux sharp
# Go back to the parent directory
cd ..
# Zip the contents of the 'sharplayer' directory 
zip -r sharplayer.zip .
# Copy the zip file to the target directory 
cp sharplayer.zip ~/environment/aws-generative-ai-document-processing-solution/deploy_code/multipagepdfa2i_imageresize/
# Provide feedback to the user
echo "Script execution completed successfully!"