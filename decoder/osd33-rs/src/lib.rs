//! Drop-in replacement for WSJT-X 3.3's `osd174_91var` (lib/ft8var/osd174_91var.f90),
//! the ordered-statistics decoder of the multithreaded FT8 decoder (MTft8).
//!
//! Port of WSJT-X code (GPL-3.0, (C) Joe Taylor K1JT, Steve Franke K9AN, Igor
//! Chernikov UA3DJY et al.). Same algorithm and arithmetic, bit for bit:
//! * codewords bit-packed ([u64; 3]), test patterns encoded with a few XORs;
//! * float sums over the same terms in the same ascending order;
//! * `indexx` and `chkcrc14a` are the Fortran routines (FFI), so ties sort alike;
//! * the second pre-processing rule's hash boxes (boxit/fetchit) keep their
//!   order and their quirks, but are not cleared per call: an epoch stamp
//!   marks the live entries (the Fortran wipes 2 MB on every call);
//! * d1 and e2sub live across test patterns as in the Fortran (an AP-masked
//!   pattern start reuses the previous values);
//! * thread-safe: MTft8 calls it from OpenMP threads; buffers are thread-local.
//!
//! The Fortran original stays in the binary as `osd174_91var_f`; OSD_CHECK=1
//! runs it on every call as well and compares the results.

use std::cell::RefCell;

const N: usize = 174;
const K: usize = 91;
type Bv = [u64; 3];

extern "C" {
    fn osd174_91var_f_(llr: *const f32, apmask: *const i8, ndeep: *mut i32, message77: *mut i8,
                       cw: *mut i8, nhardmin: *mut i32, dmin: *mut f32, nthr: *const i32,
                       progress_generation: *const i32);
    fn indexx_(arr: *const f32, n: *const i32, indx: *mut i32);
    fn chkcrc14a_(decoded: *const i8, nbadcrc: *mut i32);
    fn encode174_91_nocrc_(message: *const i8, codeword: *mut i8);
    #[link_name = "__decode_completion_module_MOD_write_decode_progress"]
    fn write_decode_progress(generation: *const i32);
}

#[inline(always)] fn bit(v: &Bv, p: usize) -> bool { (v[p >> 6] >> (p & 63)) & 1 == 1 }
#[inline(always)] fn set(v: &mut Bv, p: usize) { v[p >> 6] |= 1u64 << (p & 63); }
#[inline(always)] fn flip(v: &mut Bv, p: usize) { v[p >> 6] ^= 1u64 << (p & 63); }
#[inline(always)] fn xor(a: &Bv, b: &Bv) -> Bv { [a[0] ^ b[0], a[1] ^ b[1], a[2] ^ b[2]] }
#[inline(always)] fn and(a: &Bv, b: &Bv) -> Bv { [a[0] & b[0], a[1] & b[1], a[2] & b[2]] }
#[inline(always)] fn popcnt(a: &Bv) -> u32 { a[0].count_ones() + a[1].count_ones() + a[2].count_ones() }

fn range_mask(lo: usize, hi: usize) -> Bv {
    let mut m = [0u64; 3];
    for p in lo..hi { set(&mut m, p); }
    m
}

/// Sum of w[p] over the set bits of v, ascending - the Fortran sum minus its zero terms.
#[inline(always)]
fn wsum(v: &Bv, w: &[f32; N]) -> f32 {
    let mut s = 0.0f32;
    for (i, &word) in v.iter().enumerate() {
        let mut x = word;
        while x != 0 {
            s += w[i * 64 + x.trailing_zeros() as usize];
            x &= x - 1;
        }
    }
    s
}

#[inline(always)]
fn wsum128(m: u128, w: &[f32; N]) -> f32 {
    let mut s = 0.0f32;
    let mut x = m;
    while x != 0 {
        s += w[x.trailing_zeros() as usize];
        x &= x - 1;
    }
    s
}

/// Systematic generator: column j as a 91-bit row vector (identity | parity).
fn gen_cols() -> &'static [u128; N] {
    use std::sync::OnceLock;
    static G: OnceLock<[u128; N]> = OnceLock::new();
    G.get_or_init(|| {
        let mut cols = [0u128; N];
        for i in 0..K {
            let mut m91 = [0i8; 91];
            m91[i] = 1;
            let mut cw = [0i8; N];
            unsafe { encode174_91_nocrc_(m91.as_ptr(), cw.as_mut_ptr()) };
            for j in 0..N { if cw[j] == 1 { cols[j] |= 1u128 << i; } }
        }
        cols
    })
}

/// nextpat91var on a 0/1 array (1-based iflag, -1 when done).
fn nextpat(mi: &mut [u8; K], iorder: usize, iflag: &mut i32) {
    let mut ind: i32 = -1;
    for i in 1..K { if mi[i - 1] == 0 && mi[i] == 1 { ind = i as i32; } }
    if ind < 0 { *iflag = ind; return; }
    let ind = ind as usize;
    let mut ms = [0u8; K];
    ms[..ind - 1].copy_from_slice(&mi[..ind - 1]);
    ms[ind - 1] = 1;
    ms[ind] = 0;
    if ind + 1 < K {
        let s: usize = ms.iter().map(|&x| x as usize).sum();
        let nz = iorder - s;
        for p in (K - nz)..K { ms[p] = 1; }
    }
    *mi = ms;
    for i in 0..K { if mi[i] == 1 { *iflag = (i + 1) as i32; break; } }
}

fn mask_of(mi: &[u8; K]) -> u128 {
    let mut m = 0u128;
    for p in 0..K { if mi[p] == 1 { m |= 1u128 << p; } }
    m
}

/// boxit/fetchit hash boxes: fp[pattern] -> first entry, np[entry] -> next
/// (entries 1-based in insertion order). Epoch-stamped instead of cleared.
struct Boxes { fp: Vec<i32>, fp_ep: Vec<u32>, np: Vec<i32>, idx: Vec<(u8, u8)>, epoch: u32 }
thread_local! {
    static BOXES: RefCell<Boxes> = RefCell::new(Boxes {
        fp: vec![0; 1 << 19], fp_ep: vec![0; 1 << 19], np: vec![-1; 5001], idx: vec![(0, 0); 5001], epoch: 0 });
}

/// OSD_CHECK=1: compare every call with the Fortran original; totals at exit.
mod check {
    use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
    pub static CALLS: AtomicU64 = AtomicU64::new(0);
    pub static BAD: AtomicU64 = AtomicU64::new(0);
    pub fn on() -> bool {
        use std::sync::OnceLock;
        static ON: OnceLock<bool> = OnceLock::new();
        *ON.get_or_init(|| {
            let on = std::env::var_os("OSD_CHECK").is_some();
            if on { unsafe { atexit(report) }; }
            on
        })
    }
    extern "C" { fn atexit(f: extern "C" fn()) -> i32; }
    extern "C" fn report() {
        eprintln!("OSD_CHECK: {} calls, {} mismatches", CALLS.load(Relaxed), BAD.load(Relaxed));
    }
    pub fn count(bad: bool) { CALLS.fetch_add(1, Relaxed); if bad { BAD.fetch_add(1, Relaxed); } }
}

#[no_mangle]
pub unsafe extern "C" fn osd174_91var_(llr: *const f32, apmask: *const i8, ndeep: *mut i32,
                                      message77: *mut i8, cw_out: *mut i8, nhardmin_out: *mut i32,
                                      dmin_out: *mut f32, nthr: *const i32, progress_generation: *const i32) {
    let nd_in = *ndeep;
    osd_rs(llr, apmask, ndeep, message77, cw_out, nhardmin_out, dmin_out, *progress_generation);
    if check::on() {
        let (mut m, mut c, mut nh, mut dm, mut nd) = ([0i8; 77], [0i8; N], 0i32, 0f32, nd_in);
        let pg: i32 = 0;                                  // no second round of progress ticks
        osd174_91var_f_(llr, apmask, &mut nd, m.as_mut_ptr(), c.as_mut_ptr(), &mut nh, &mut dm, nthr, &pg);
        let rc = std::slice::from_raw_parts(cw_out, N);
        let rm = std::slice::from_raw_parts(message77, 77);
        let bad = rc != &c[..] || rm != &m[..] || nh != *nhardmin_out
            || dm.to_bits() != (*dmin_out).to_bits() || nd != *ndeep;
        check::count(bad);
    }
}

unsafe fn osd_rs(llr: *const f32, apmask: *const i8, ndeep: *mut i32, message77: *mut i8,
                 cw_out: *mut i8, nhardmin_out: *mut i32, dmin_out: *mut f32, pgen: i32) {
    let llr = std::slice::from_raw_parts(llr, N);
    let apmask = std::slice::from_raw_parts(apmask, N);
    let gen = gen_cols();
    let progress = || write_decode_progress(&pgen);

    let mut absrx0 = [0f32; N];
    for i in 0..N { absrx0[i] = llr[i].abs(); }
    let mut indx = [0i32; N];
    indexx_(absrx0.as_ptr(), &(N as i32), indx.as_mut_ptr());

    let mut indices = [0usize; N];
    let mut rows = [[0u64; 3]; K];
    for c in 0..N {
        indices[c] = (indx[N - 1 - c] - 1) as usize;
        let mut col = gen[indices[c]];
        while col != 0 {
            set(&mut rows[col.trailing_zeros() as usize], c);
            col &= col - 1;
        }
    }
    for id in 0..K {
        for icol in id..(K + 20) {
            if bit(&rows[id], icol) {
                if icol != id {
                    for r in rows.iter_mut() {
                        if bit(r, id) != bit(r, icol) { flip(r, id); flip(r, icol); }
                    }
                    indices.swap(id, icol);
                }
                let pivot = rows[id];
                for ii in 0..K {
                    if ii != id && bit(&rows[ii], id) { rows[ii] = xor(&rows[ii], &pivot); }
                }
                break;
            }
        }
    }

    let mut hdec: Bv = [0; 3];
    let mut absrx = [0f32; N];
    let mut apm: u128 = 0;
    for p in 0..N {
        let o = indices[p];
        if llr[o] >= 0.0 { set(&mut hdec, p); }
        absrx[p] = absrx0[o];
        if p < K && apmask[o] == 1 { apm |= 1u128 << p; }
    }
    let m0: u128 = (hdec[0] as u128) | (((hdec[1] as u128) & ((1u128 << (K - 64)) - 1)) << 64);
    let enc = |m: u128| -> Bv {
        let mut c = [0u64; 3];
        let mut x = m;
        while x != 0 {
            c = xor(&c, &rows[x.trailing_zeros() as usize]);
            x &= x - 1;
        }
        c
    };
    let c0 = enc(m0);
    let nx = xor(&c0, &hdec);
    let mut nhardmin = popcnt(&nx) as i32;
    let mut dmin = wsum(&nx, &absrx);
    let mut cw = c0;

    if *ndeep != 0 {
        if *ndeep > 5 { *ndeep = 5; }
        let (nord, npre1, npre2, nt, ntheta, ntau): (usize, usize, usize, usize, u32, usize) = match *ndeep {
            1 => (1, 0, 0, 40, 12, 0),
            2 => (1, 1, 0, 40, 12, 0),
            3 => (1, 1, 1, 40, 12, 14),
            4 => (2, 1, 0, 40, 12, 19),
            _ => (2, 1, 1, 40, 12, 19),     // 5 (negative ndeep falls through all ifs in the Fortran)
        };
        let negative = *ndeep < 0;           // Fortran: no branch taken, nord etc. stay 0 -> no search
        let par = range_mask(K, N);
        let ntm = range_mask(K, K + nt);
        let pw = { let mut w = [0f32; N]; w[K..].copy_from_slice(&absrx[K..]); w };
        let mut d1 = 0.0f32;                 // persists across patterns (Fortran: set once at the top)
        let mut e2sub: Bv = [0; 3];          // persists likewise

        if !negative {
            for iorder in 1..=nord {
                let mut progress_work = 0u32;
                let mut misub = [0u8; K];
                for p in (K - iorder)..K { misub[p] = 1; }
                let mut iflag: i32 = (K - iorder + 1) as i32;
                while iflag >= 0 {
                    progress_work += 1;
                    if progress_work % 32 == 0 { progress(); }
                    let iend = if iorder == nord && npre1 == 0 { iflag } else { 1 };
                    let msub = mask_of(&misub);
                    let csub = xor(&c0, &enc(msub));
                    let mut n1 = iflag;
                    while n1 >= iend {
                        let b = (n1 - 1) as usize;
                        let mi = msub | (1u128 << b);
                        if apm & mi != 0 { n1 -= 1; continue; }
                        let first = n1 == iflag;
                        // me = m0 ^ mi; its codeword is csub, plus row b when b is new
                        let in_sub = msub >> b & 1 == 1;
                        let nd1kpt;
                        let e2;
                        if first {
                            let ce = if in_sub { csub } else { xor(&csub, &rows[b]) };
                            e2sub = and(&xor(&ce, &hdec), &par);
                            e2 = e2sub;
                            nd1kpt = popcnt(&and(&e2sub, &ntm)) + 1;
                            d1 = wsum128(mi, &absrx);
                        } else {
                            e2 = xor(&e2sub, &and(&rows[b], &par));
                            nd1kpt = popcnt(&and(&e2, &ntm)) + 2;
                        }
                        if nd1kpt <= ntheta {
                            let ce = if in_sub { csub } else { xor(&csub, &rows[b]) };
                            let nxor = xor(&ce, &hdec);
                            let dd = if first {
                                d1 + wsum(&e2sub, &pw)
                            } else {
                                let t = if bit(&nxor, b) { absrx[b] } else { 0.0 };
                                (d1 + t) + wsum(&e2, &pw)
                            };
                            if dd < dmin {
                                dmin = dd;
                                cw = ce;
                                nhardmin = popcnt(&nxor) as i32;
                            }
                        }
                        n1 -= 1;
                    }
                    nextpat(&mut misub, iorder, &mut iflag);
                }
            }

            if npre2 == 1 {
                // ntau leading parity bits of each row, first bit most significant
                let mut rp = [0u32; K];
                for (i, r) in rows.iter().enumerate() {
                    let mut v = 0u32;
                    for t in 0..ntau { if bit(r, K + t) { v |= 1 << (ntau - 1 - t); } }
                    rp[i] = v;
                }
                BOXES.with(|bx| {
                    let bx = &mut *bx.borrow_mut();
                    bx.epoch = bx.epoch.wrapping_add(1);
                    if bx.epoch == 0 { bx.fp_ep.iter_mut().for_each(|e| *e = 0); bx.epoch = 1; }
                    let ep = bx.epoch;
                    let mut npindex = 0usize;
                    for i1 in (1..=K).rev() {
                        if i1 % 8 == 0 { progress(); }
                        for i2 in (1..i1).rev() {
                            npindex += 1;
                            bx.idx[npindex] = (i1 as u8, i2 as u8);
                            bx.np[npindex] = -1;
                            let ipat = (rp[i1 - 1] ^ rp[i2 - 1]) as usize;
                            if bx.fp_ep[ipat] != ep {
                                bx.fp_ep[ipat] = ep;
                                bx.fp[ipat] = npindex as i32;
                            } else {
                                let mut ip = bx.fp[ipat] as usize;
                                while bx.np[ip] != -1 { ip = bx.np[ip] as usize; }
                                bx.np[ip] = npindex as i32;
                            }
                        }
                    }
                    // the second pre-processing rule, with fetchit91var's state
                    let mut lastpat: i64 = -1;
                    let mut inext: i32 = -1;
                    let mut misub = [0u8; K];
                    for p in (K - nord)..K { misub[p] = 1; }
                    let mut iflag: i32 = (K - nord + 1) as i32;
                    let mut progress_work = 0u32;
                    while iflag >= 0 {
                        progress_work += 1;
                        if progress_work % 32 == 0 { progress(); }
                        let msub = mask_of(&misub);
                        let ce0 = xor(&c0, &enc(msub));
                        let e2s = xor(&ce0, &hdec);          // only its parity bits are read
                        let mut base = 0u32;
                        for t in 0..ntau { if bit(&e2s, K + t) { base |= 1 << (ntau - 1 - t); } }
                        for i2 in 0..=ntau {
                            let ipat = if i2 > 0 { base ^ (1 << (ntau - i2)) } else { base } as i64;
                            loop {
                                // fetchit91var
                                let index = if bx.fp_ep[ipat as usize] == ep { bx.fp[ipat as usize] } else { -1 };
                                let (in1, in2);
                                if lastpat != ipat && index > 0 {
                                    (in1, in2) = (bx.idx[index as usize].0 as i32, bx.idx[index as usize].1 as i32);
                                    inext = bx.np[index as usize];
                                } else if lastpat == ipat && inext > 0 {
                                    (in1, in2) = (bx.idx[inext as usize].0 as i32, bx.idx[inext as usize].1 as i32);
                                    inext = bx.np[inext as usize];
                                } else {
                                    (in1, in2) = (-1, -1);
                                    inext = -1;
                                }
                                lastpat = ipat;
                                if !(in1 > 0 && in2 > 0) { break; }
                                let mi = msub | (1u128 << (in1 - 1)) | (1u128 << (in2 - 1));
                                if (mi.count_ones() as usize) < nord + npre1 + npre2 || apm & mi != 0 { break; }
                                let ce = xor(&c0, &enc(mi));
                                let nxor = xor(&ce, &hdec);
                                let dd = wsum(&nxor, &absrx);
                                if dd < dmin {
                                    dmin = dd;
                                    cw = ce;
                                    nhardmin = popcnt(&nxor) as i32;
                                }
                            }
                        }
                        nextpat(&mut misub, nord, &mut iflag);
                    }
                });
            }
        }
    }

    let mut out = [0i8; N];
    for p in 0..N { if bit(&cw, p) { out[indices[p]] = 1; } }
    std::ptr::copy_nonoverlapping(out.as_ptr(), cw_out, N);
    let mut nbadcrc: i32 = 0;
    chkcrc14a_(out.as_ptr(), &mut nbadcrc);
    std::ptr::copy_nonoverlapping(out.as_ptr(), message77, 77);
    if nbadcrc == 1 { nhardmin = -nhardmin; }
    *nhardmin_out = nhardmin;
    *dmin_out = dmin;
}
