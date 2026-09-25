//! `cargo run --example parse -- "<url>"` — imprime el IR JSON o el error.
fn main() {
    let src = std::env::args().nth(1).expect("usage: parse <url>");
    match qsurl::parse_to_ir(&src) {
        Ok(ir) => println!("{}", serde_json::to_string_pretty(&ir).unwrap()),
        Err(e) => {
            eprintln!("{}", e.to_json());
            std::process::exit(1);
        }
    }
}
