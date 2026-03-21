#!/usr/bin/env sh
export OLDSHELL=$SHELL
nix develop --impure -c $SHELL
# N.B.
# --impure  - allows devenv to access state data when running with flakes
# -c $SHELl - support fish, zsh, etc. (default shell is bash)
