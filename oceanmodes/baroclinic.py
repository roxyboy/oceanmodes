# python 3 forward compatibility
from __future__ import absolute_import, division, print_function
from builtins import *
#
import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import eigs

def _maybe_truncate_above_topography(z, f):
    "Return truncated versions of z and f if f is masked or nan."
    # checks on shapes of stuff
    if not z.shape == f.shape:
        raise ValueError('z and f must have the same length')

    fm = np.ma.masked_invalid(f)
    if fm.mask.sum()==0:
        return z, f

    # check to make sure the mask is only at the bottom
    #if not fm.mask[-1]:
    #    raise ValueError('topography should be at the bottom of the column')
    # the above check was redundant with this one
    if fm.mask[-1] and np.diff(fm.mask).sum() != 1:
        raise ValueError('topographic mask should be monotonic')

    zout = z[~fm.mask]
    fout = fm.compressed()
    return zout, fout

def neutral_modes_from_N2_profile(z, N2, f0, depth=None, **kwargs):
    """Calculate baroclinic neutral modes from a profile of buoyancy frequency.

    Solves the eigenvalue problem

        \frac{d}{dz}\left ( \frac{f_0^2}{N^2} \frac{d \phi}{d z} \right )
        = -\frac{1}{L_d^2} \phi

    With the boundary conditions

        \frac{d \phi}{d z} = 0

    at the top and bottom.

    Parameters
    ----------
    z : array_like
        The depths at which N2 is given. Starts shallow, increases
        positive downward.
    N2 : array_like
        The squared buoyancy frequency (units s^-2). Points below topography
        should be masked or nan.
    f0 : float
        Coriolis parameter.
    depth : float, optional
        Total water column depth. If missing will be inferred from z.
    kwargs : optional
        Additional parameters to pass to scipy.sparse.linalg.eigs for
        eigenvalue computation

    Returns
    -------
    zc : array_like
        The depths at which phi is defined. Different from z.
    L_d : array_like
        deformation radii, sorted descending
    phi : array_like
        vertical modes
    """
    nz_orig = len(z)
    z, N2 = _maybe_truncate_above_topography(z, N2)
    return _neutral_modes_from_N2_profile_raw(
                                    z, N2, f0, depth=depth, **kwargs)
    # does it make sense to re-pad output?

def _neutral_modes_from_N2_profile_raw(z, N2, f0, depth=None, **kwargs):
    nz = len(z)

    ### vertical discretization ###

    # ~~~~~ zf[0]==0, phi[0] ~~~~
    #
    # ----- zc[0], N2[0] --------
    #
    # ----- zf[1], phi[1] -------
    # ...
    # ---- zc[nz-1], N2[nz-1] ---
    #
    # ~~~~ zf[nz], phi[nz] ~~~~~~

    # just for notation's sake
    # (user shouldn't worry about discretization)
    zc = z
    dzc = np.hstack(np.diff(zc))
    # make sure z is increasing
    if not np.all(dzc > 0):
        raise ValueError('z should be monotonically increasing')
    if depth is None:
        depth = z[-1] + dzc[-1]/2
    else:
        if depth <= z[-1]:
            raise ValueError('depth should not be less than maximum z')

    dztop = zc[0]
    dzbot = depth - zc[-1]

    # put the phi points right between the N2 points
    zf = np.hstack([0, 0.5*(zc[1:]+zc[:-1]), depth ])
    dzf = np.diff(zf)

    # We want a matrix representation of the operator such that
    #    g = f0**2 * np.dot(L, f)
    # This can be put in "tridiagonal" form
    # 1) first derivative of f (defined at zf points, maps to zc points)
    #    dfdz[i] = (f[i] - f[i+1]) /  dzf[i]
    # 2) now we are at zc points, so multipy directly by f0^2/N^2
    #    q[i] = dfdz[i] / N2[i]
    # 3) take another derivative to get back to f points
    #    g[i] = (q[i-1] - q[i]) / dzc[i-1]
    # boundary condition is enforced by assuming q = 0 at zf = 0
    #    g[0] = (0 - q[0]) / dztop
    #    g[nz] = (q[nz-1] - 0) / dzbot
    # putting it all together gives
    #    g[i] = ( ( (f[i-1] - f[i]) / (dzf[i-1] * N2[i-1]) )
    #            -( (f[i] - f[i+1]) / (dzf[i] * N2[i])) ) / dzc[i-1]
    # which we can rewrite as
    #    g[i] = ( a*f[i-1] + b*f[i] +c*f[i+1] )
    # where
    #    a = (dzf[i-1] * N2[i-1] * dzc[i-1])**-1
    #    b = -( ((dzf[i-1] * N2[i-1]) + (dzf[i] * N2[i])) * dzc[i-1] )**-1
    #    c = (dzf[i] * N2[i] * dzc[i-1])**-1
    # for the boundary conditions we have
    #    g[0] =  (-f[0] + f[1]) /  (dzf[0] * N2[0] * dztop)
    #    g[nz] = (f[nz-1] - f[nz]) /  (dzf[nz-1] * N2[nz-1] *dzbot)
    # which we can rewrite as
    #    g[0] = (-a*f[0] + a*f[1])
    #           a = (dzf[0] * N2[0])**-1
    #    g[nz] = (b*f[nz-1] - b*f[nz])
    #           b = (dzf[nz-1] * N2[nz-1])**-1

    # now turn all of that into a sparse matrix

    L = lil_matrix((nz+1, nz+1), dtype=np.float64)
    for i in range(1,nz):
        a = (dzf[i-1] * N2[i-1] * dzc[i-1])**-1
        b = -(dzf[i-1] * N2[i-1]* dzc[i-1])**-1 - (dzf[i] * N2[i] * dzc[i-1])**-1
        c = (dzf[i] * N2[i] * dzc[i-1])**-1
        L[i,i-1:i+2] = [a,b,c]
    a = (dzf[0] * N2[0] * dztop)**-1
    L[0,:2] = [-a, a]
    b = (dzf[nz-1] * N2[nz-1] * dzbot)**-1
    L[nz,-2:] = [b, -b]

    # this gets the eignevalues and eigenvectors
    w, v = eigs(L, which='SM')

    # eigs returns complex values. Make sure they are actually real
    tol = 1e-20
    np.testing.assert_allclose(np.imag(v), 0, atol=tol)
    np.testing.assert_allclose(np.imag(w), 0, atol=tol)
    w = np.real(w)
    v = np.real(v)

    # they are often sorted and normalized, but not always
    # so we have to do that here
    j = np.argsort(w)[::-1]
    w = w[j]
    v = v[:,j]

    Ld = (-w)**-0.5 / f0

    return zf, Ld, v
