# Contributing

Thank you for your interest in contributing to Webots-server!
The following is a set of guidelines for helping you to contribute to Webots-server.

## Required skills

You don't need to be an expert in robotics or software development to become a contributor.
Depending on your skills, your contribution may address different parts of Webots-server:

- Bug reporting: [A precise description](https://github.com/cyberbotics/webots-server/issues/new) of a reproducible bug is very helpful to us.
- Technical English writing: [documentation pages](https://github.com/cyberbotics/webots/tree/released/docs).

In any case, you should have a minimal knowledge of GitHub to fork our repository and create a Pull Request that we will review and hopefully accept.

## Create a Pull Request

1. Fork the repository: https://help.github.com/articles/fork-a-repo
2. Create a branch in your fork: https://help.github.com/articles/creating-and-deleting-branches-within-your-repository
3. Pull the branch as a pull request targeting `cyberbotics:webots-server@main`: https://help.github.com/articles/creating-a-pull-request-from-a-fork
4. Wait for our review of your pull request.

Our git workflow is explained in detail [here](https://github.com/cyberbotics/webots/wiki/Git-workflow/).

## Development Guideline

* Follow our [Coding Style](https://github.com/cyberbotics/webots/wiki/Coding-Style/).
* Avoid comitting files that exist elsewhere. Instead you should link to the source of these files.
* Avoid comitting files that can be re-created from other files using a Makefile, a script or a compiler.
