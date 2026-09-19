prepare_layout() {
    # Root owns the hierarchy. Only the two explicit exchange directories are
    # writable by the API container; the group may traverse their parents.
    for host_directory in "$ROBOPARK_VAR" "$ROBOPARK_VAR/ops"; do
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 0:10001 "$host_directory"
        chmod 750 "$host_directory"
    done
    for private_name in state compose rollbacks; do
        host_directory=$ROBOPARK_VAR/ops/$private_name
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 0:0 "$host_directory"
        chmod 700 "$host_directory"
    done
    for private_name in compose restores; do
        host_directory=$ROBOPARK_VAR/ops/state/$private_name
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 0:0 "$host_directory"
        chmod 700 "$host_directory"
    done
    for exchange_name in inbox artifacts; do
        host_directory=$ROBOPARK_VAR/ops/$exchange_name
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 10001:10001 "$host_directory"
        chmod 700 "$host_directory"
    done
    for data_name in data api-ops; do
        host_directory=$ROBOPARK_VAR/$data_name
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 10001:10001 "$host_directory"
        chmod 700 "$host_directory"
    done
    host_directory=$ROBOPARK_VAR/ops/public
    [ ! -L "$host_directory" ] || die symlinked_host_directory
    mkdir -p "$host_directory"
    chown 0:0 "$host_directory"
    chmod 755 "$host_directory"

    for host_directory in "$ROBOPARK_VAR/diagnostics" "${ROBOPARK_ROOT%/}/var/log/robopark"; do
        [ ! -L "$host_directory" ] || die symlinked_host_directory
        mkdir -p "$host_directory"
        chown 0:0 "$host_directory"
        chmod 700 "$host_directory"
    done

}
